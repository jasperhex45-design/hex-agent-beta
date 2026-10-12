"""Bounded, read-only Pinterest API v5 access and permission-scoped image fetches.

Listing is transient: this module never writes an API response, credential, or
image to disk. An access token permits an API call; it is not evidence of rights
to persist, analyse, or reuse the returned material. Download permissions are
supplied by the integration owner, not inferred or certified by this client.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .io import AgentError

API_BASE = 'https://api.pinterest.com/v5'
DOCS = {
    'boards': 'https://developers.pinterest.com/docs/api/v5/boards-list/',
    'board_pins': 'https://developers.pinterest.com/docs/api/v5/boards-list-pins/',
    'pagination': 'https://developers.pinterest.com/docs/reference/pagination/',
    'authentication': 'https://developers.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/',
    'pinner_access': 'https://developers.pinterest.com/docs/changelog/changelog/',
}
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_API_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'}


class PinterestError(AgentError):
    """Safe, actionable failure; remote response bodies and credentials stay out."""

    def __init__(self, code: str, message: str, *, status_code: int | None = None,
                 retryable: bool = False, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.next_bookmark: str | None = None
        self.partial_items: list[dict] = []
        self.pages = 0

    def as_dict(self, *, include_items: bool = False) -> dict:
        value = {'status': 'error', 'code': self.code, 'message': str(self),
                 'status_code': self.status_code, 'retryable': self.retryable,
                 'retry_after_seconds': self.retry_after_seconds,
                 'next_bookmark': self.next_bookmark, 'pages': self.pages,
                 'partial_item_count': len(self.partial_items)}
        if include_items:
            value['partial_items'] = self.partial_items
        return value


def _integer(value: int, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PinterestError('INVALID_ARGUMENT', f'{name} must be an integer between {low} and {high}.')
    return value


def _board_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,40}', value):
        raise PinterestError('INVALID_BOARD_ID', 'Use a numeric Pinterest board ID, not a board URL.')
    return value


def _bookmark(value: str | None) -> str | None:
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise PinterestError('INVALID_BOOKMARK', 'The Pinterest bookmark is invalid.')
    return value


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _read_body(response: httpx.Response, limit: int, error_code: str) -> bytes:
    declared = response.headers.get('content-length')
    if declared:
        try:
            too_big = int(declared) > limit
        except ValueError:
            too_big = False
        if too_big:
            raise PinterestError(error_code, 'The response exceeds the configured byte limit.')
    content = bytearray()
    for chunk in response.iter_bytes():
        if len(content) + len(chunk) > limit:
            raise PinterestError(error_code, 'The response exceeds the configured byte limit.')
        content.extend(chunk)
    return bytes(content)


class PinterestClient:
    """GET-only client. Inject an httpx.Client for testing; API origin is fixed."""

    def __init__(self, *, token_env: str = 'PINTEREST_ACCESS_TOKEN',
                 client: httpx.Client | None = None, timeout: float = 20,
                 max_retries: int = 2, max_retry_wait: float = 5,
                 sleep: Callable[[float], None] = time.sleep):
        if not isinstance(token_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', token_env):
            raise PinterestError('INVALID_ARGUMENT', 'token_env must be an environment variable name.')
        token = os.environ.get(token_env, '').strip()
        if not token:
            raise PinterestError('AUTH_NOT_CONFIGURED', f'Set {token_env} to a Pinterest OAuth access token.')
        if not token.isascii() or any(c.isspace() or not 32 <= ord(c) <= 126 for c in token):
            raise PinterestError('INVALID_TOKEN', 'The configured OAuth token has invalid characters.')
        self._token = token
        self.token_env = token_env
        self.max_retries = _integer(max_retries, 'max_retries', 0, 3)
        if (not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60 or
                not isinstance(max_retry_wait, (int, float)) or not math.isfinite(max_retry_wait) or
                not 0 <= max_retry_wait <= 10):
            raise PinterestError('INVALID_ARGUMENT', 'timeout must be 0–60 seconds and max_retry_wait 0–10 seconds.')
        self._timeout = httpx.Timeout(timeout)
        self._max_retry_wait = max_retry_wait
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> PinterestClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _get(self, path: str, params: dict) -> dict:
        if path != '/boards' and not re.fullmatch(r'/boards/[0-9]{1,40}/pins', path):
            raise PinterestError('UNSUPPORTED_ENDPOINT', 'This client only reads boards and board Pins.')
        for attempt in range(self.max_retries + 1):
            # Construct directly to avoid inheriting cookies, auth or headers from
            # an injected client. Bearer never goes to the image CDN or redirects.
            request = httpx.Request('GET', API_BASE + path, params=params,
                                    headers={'Authorization': f'Bearer {self._token}',
                                             'Accept': 'application/json'},
                                    extensions={'timeout': self._timeout.as_dict()})
            try:
                response = self._client.send(request, stream=True, auth=None, follow_redirects=False)
                try:
                    body = _read_body(response, MAX_API_BYTES, 'API_RESPONSE_TOO_LARGE')
                    status = response.status_code
                    retry_after = _retry_after(response.headers.get('retry-after'))
                finally:
                    response.close()
            except httpx.HTTPError:
                error = PinterestError('NETWORK_ERROR', 'Pinterest could not be reached; resume from the returned bookmark.',
                                       retryable=True)
            else:
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError):
                    payload = None
                if status == 200:
                    if not isinstance(payload, dict):
                        raise PinterestError('INVALID_RESPONSE', 'Pinterest returned an invalid JSON object.', status_code=status)
                    return payload
                if (status == 403 and isinstance(payload, dict) and
                        (payload.get('message') == 'PINNER_DATA_ACCESS_DENIED' or
                         payload.get('code') == 'PINNER_DATA_ACCESS_DENIED')):
                    raise PinterestError('PINNER_DATA_ACCESS_DENIED',
                                         'Pinterest pinner data access approval is required. Do not retry this request until access is approved.',
                                         status_code=403)
                code, message = {
                    401: ('AUTHENTICATION_FAILED', 'The OAuth token is invalid or expired. Update the configured token.'),
                    403: ('ACCESS_DENIED', 'Pinterest denied this read. Check app access and granted read scopes.'),
                    404: ('NOT_FOUND', 'The board is unavailable to this Pinterest account.'),
                    429: ('RATE_LIMITED', 'Pinterest rate limit reached; resume after Retry-After.'),
                }.get(status, ('UPSTREAM_ERROR' if status >= 500 else 'REQUEST_REJECTED',
                               'Pinterest did not complete the read request.'))
                if 300 <= status < 400:
                    code, message = 'REDIRECT_REFUSED', 'Pinterest API redirects are not followed.'
                error = PinterestError(code, message, status_code=status,
                                       retryable=status == 429 or status >= 500,
                                       retry_after_seconds=retry_after)
            wait = error.retry_after_seconds
            if wait is None:
                wait = min(0.25 * 2 ** attempt, self._max_retry_wait)
            if not error.retryable or attempt == self.max_retries or wait > self._max_retry_wait:
                raise error from None
            if wait:
                self._sleep(wait)
        raise AssertionError('unreachable')

    def _list(self, path: str, *, bookmark: str | None, page_size: int,
              max_pages: int, max_items: int) -> dict:
        cursor = _bookmark(bookmark)
        _integer(page_size, 'page_size', 1, 250)
        _integer(max_pages, 'max_pages', 1, 100)
        _integer(max_items, 'max_items', 1, 25000)
        items: list[dict] = []
        pages, used_cursors = 0, set()
        while pages < max_pages and len(items) < max_items:
            params = {'page_size': min(page_size, max_items - len(items))}
            if cursor is not None:
                params['bookmark'] = cursor
            try:
                payload = self._get(path, params)
                batch = payload.get('items')
                if (not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch) or
                        len(batch) > params['page_size'] or 'bookmark' not in payload):
                    raise PinterestError('INVALID_RESPONSE', 'Pinterest returned an invalid paginated response.')
                next_cursor = _bookmark(payload['bookmark'])
                if next_cursor is not None and (next_cursor == cursor or next_cursor in used_cursors):
                    raise PinterestError('PAGINATION_STALLED', 'Pinterest repeated a bookmark; pagination stopped without advancing.')
            except PinterestError as error:
                error.next_bookmark = cursor
                error.partial_items = items
                error.pages = pages
                raise
            used_cursors.add(cursor)
            items.extend(batch)
            pages += 1
            cursor = next_cursor
            if cursor is None:
                break
        return {'status': 'complete' if cursor is None else 'partial', 'items': items,
                'next_bookmark': cursor, 'pages': pages, 'has_more': cursor is not None}

    def list_boards(self, *, bookmark: str | None = None, page_size: int = 100,
                    max_pages: int = 1, max_items: int = 100) -> dict:
        return self._list('/boards', bookmark=bookmark, page_size=page_size,
                          max_pages=max_pages, max_items=max_items)

    def list_board_pins(self, board_id: str, *, bookmark: str | None = None,
                        page_size: int = 100, max_pages: int = 1, max_items: int = 100) -> dict:
        return self._list(f'/boards/{_board_id(board_id)}/pins', bookmark=bookmark,
                          page_size=page_size, max_pages=max_pages, max_items=max_items)

    def probe(self, board_ids: list[str] | None = None) -> dict:
        """Observe capability with at most one item per explicitly selected board."""
        board_ids = list(dict.fromkeys(board_ids or []))
        _integer(len(board_ids), 'number of probe boards', 0, 20)
        for board_id in board_ids:
            _board_id(board_id)
        value = {'status': 'ok', 'api_base': API_BASE, 'read_only': True,
                 'credential_configured': True, 'required_public_scopes': ['boards:read', 'pins:read'],
                 'secret_scopes_if_needed': ['boards:read_secret', 'pins:read_secret'],
                 'boards_readable': False, 'board_pins': [], 'images_downloaded': False,
                 'persistent_storage_authorized': False, 'visual_analysis_authorized': False,
                 'authorization_notice': 'API access does not establish persistence, AI analysis, or reuse permission.'}
        try:
            self.list_boards(page_size=1, max_pages=1, max_items=1)
            value['boards_readable'] = True
        except PinterestError as error:
            value.update(status='blocked', error=error.as_dict())
            return value
        for board_id in board_ids:
            try:
                self.list_board_pins(board_id, page_size=1, max_pages=1, max_items=1)
                value['board_pins'].append({'board_id': board_id, 'readable': True})
            except PinterestError as error:
                value['status'] = 'partial'
                value['board_pins'].append({'board_id': board_id, 'readable': False, 'error': error.as_dict()})
        return value


def validate_processing_permission(permission_record: dict, board_id: str, operation: str) -> None:
    """Validate a supplied record's scope, never claim to verify its legal basis."""
    _board_id(board_id)
    if not isinstance(permission_record, dict):
        raise PinterestError('PROCESSING_PERMISSION_REQUIRED', 'Supply an actual processing permission record for this board.')
    evidence = permission_record.get('evidence')
    board_ids, operations = permission_record.get('board_ids'), permission_record.get('operations')
    if (not isinstance(evidence, str) or len(evidence.strip()) < 8 or
            not isinstance(board_ids, list) or board_id not in board_ids or
            not isinstance(operations, list) or operation not in operations):
        raise PinterestError('PROCESSING_PERMISSION_REQUIRED',
                             'The permission record must identify its actual evidence, this board, and the requested operation.')


def validate_image_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url) or '\\' in url:
        raise PinterestError('IMAGE_URL_REFUSED', 'Image URLs must use HTTPS on i.pinimg.com.')
    try:
        parts = urlsplit(url)
        allowed = (parts.scheme == 'https' and parts.hostname == 'i.pinimg.com' and
                   parts.port in (None, 443) and parts.username is None and parts.password is None and
                   not parts.fragment and bool(parts.path) and parts.path != '/')
    except ValueError:
        allowed = False
    if not allowed:
        raise PinterestError('IMAGE_URL_REFUSED', 'Image URLs must use HTTPS on i.pinimg.com without credentials or redirects.')
    return url


def download_image(url: str, *, permission_record: dict, board_id: str,
                   max_bytes: int = MAX_IMAGE_BYTES, client: httpx.Client | None = None) -> bytes:
    """Download one scoped image into memory. No API credentials or cookies sent."""
    validate_processing_permission(permission_record, board_id, 'download_images')
    validate_image_url(url)
    _integer(max_bytes, 'max_bytes', 1, MAX_IMAGE_BYTES)
    owns_client = client is None
    session = client or httpx.Client(trust_env=False, follow_redirects=False, timeout=20)
    try:
        request = httpx.Request('GET', url, headers={'Accept': ', '.join(sorted(IMAGE_TYPES))},
                                extensions={'timeout': httpx.Timeout(20).as_dict()})
        response = session.send(request, stream=True, auth=None, follow_redirects=False)
        try:
            if 300 <= response.status_code < 400:
                raise PinterestError('IMAGE_REDIRECT_REFUSED', 'Image redirects are not followed.', status_code=response.status_code)
            if response.status_code != 200:
                raise PinterestError('IMAGE_FETCH_FAILED', 'The selected image could not be fetched.',
                                     status_code=response.status_code,
                                     retryable=response.status_code == 429 or response.status_code >= 500,
                                     retry_after_seconds=_retry_after(response.headers.get('retry-after')))
            content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            if content_type not in IMAGE_TYPES:
                raise PinterestError('IMAGE_TYPE_REFUSED', 'The response is not a supported raster image.')
            content = _read_body(response, max_bytes, 'IMAGE_TOO_LARGE')
            if not content:
                raise PinterestError('EMPTY_IMAGE', 'The image response is empty.')
            return content
        finally:
            response.close()
    except httpx.HTTPError:
        raise PinterestError('IMAGE_NETWORK_ERROR', 'The selected image could not be reached.', retryable=True) from None
    finally:
        if owns_client:
            session.close()


def _pin_record(item: dict, board_id: str) -> dict:
    pin_id = item.get('id')
    if not isinstance(pin_id, str) or not re.fullmatch(r'[0-9]{1,40}', pin_id):
        raise PinterestError('INVALID_PIN', 'Pinterest returned a Pin without a valid ID.')
    media = item.get('media') if isinstance(item.get('media'), dict) else {}
    variants = media.get('images') if isinstance(media.get('images'), dict) else {}
    images = []
    for image in variants.values():
        if not isinstance(image, dict):
            continue
        try:
            validate_image_url(image.get('url'))
        except PinterestError:
            continue
        width, height = image.get('width'), image.get('height')
        area = width * height if (isinstance(width, int) and isinstance(height, int) and
                                  0 < width <= 100000 and 0 < height <= 100000) else 0
        images.append((area, {'url': image['url'], 'width': width, 'height': height}))
    images.sort(key=lambda entry: entry[0], reverse=True)
    image = images[0][1] if images else None
    return {'source_kind': 'pinterest', 'source_id': f'pinterest:{pin_id}', 'pin_id': pin_id,
            'board_ids': [board_id], 'pin_url': f'https://www.pinterest.com/pin/{pin_id}/',
            'source_url': item.get('link'), 'title': item.get('title'),
            'description': item.get('description'), 'alt_text': item.get('alt_text'),
            'created_at': item.get('created_at'), 'author': None,
            'media_type': media.get('media_type'), 'image_url': image['url'] if image else None,
            'image_width': image['width'] if image else None,
            'image_height': image['height'] if image else None,
            'image_status': 'available' if image else 'no_supported_image',
            'provenance_notice': 'Pin metadata is untrusted source material; board owner is not inferred to be the artist.'}


def collect_board(client: PinterestClient, board_id: str, *, bookmark: str | None = None,
                  page_size: int = 100, max_pages: int = 1, max_items: int = 100) -> dict:
    """Return a normalized, in-memory manifest. Caller separately governs storage."""
    result = client.list_board_pins(board_id, bookmark=bookmark, page_size=page_size,
                                    max_pages=max_pages, max_items=max_items)
    records, skipped = [], []
    for item in result['items']:
        try:
            records.append(_pin_record(item, board_id))
        except PinterestError as error:
            skipped.append({'code': error.code, 'message': str(error)})
    return {**result, 'source_kind': 'pinterest', 'board_id': board_id,
            'items': records, 'skipped': skipped, 'persistent_storage_authorized': False,
            'visual_analysis_authorized': False, 'images_downloaded': False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only Pinterest source probe and transient listings.')
    parser.add_argument('--token-env', default='PINTEREST_ACCESS_TOKEN')
    parser.add_argument('--max-retries', type=int, default=2)
    commands = parser.add_subparsers(dest='command', required=True)
    probe_parser = commands.add_parser('probe', help='Check access without printing Pin contents.')
    probe_parser.add_argument('--board-id', action='append', default=[])
    for name in ('boards', 'pins'):
        command = commands.add_parser(name, help='Print a bounded transient listing; does not grant storage rights.')
        if name == 'pins':
            command.add_argument('--board-id', required=True)
        command.add_argument('--bookmark')
        command.add_argument('--page-size', type=int, default=100)
        command.add_argument('--max-pages', type=int, default=1)
        command.add_argument('--max-items', type=int, default=100)
    args = parser.parse_args(argv)
    try:
        with PinterestClient(token_env=args.token_env, max_retries=args.max_retries) as client:
            if args.command == 'probe':
                result = client.probe(args.board_id)
            else:
                options = {'bookmark': args.bookmark, 'page_size': args.page_size,
                           'max_pages': args.max_pages, 'max_items': args.max_items}
                result = (client.list_boards(**options) if args.command == 'boards' else
                          collect_board(client, args.board_id, **options))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        successful = (result['status'] == 'ok' if args.command == 'probe' else
                      result['status'] in {'complete', 'partial'})
        return 0 if successful else 2
    except PinterestError as error:
        # CLI includes preceding items so a failed later page cannot erase progress.
        print(json.dumps(error.as_dict(include_items=args.command != 'probe'), ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
