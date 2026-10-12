"""Public entry point for the read-only Pinterest source adapter."""
from liu_agent.pinterest_source import (
    PinterestClient, PinterestError, collect_board, download_image, main,
    validate_image_url, validate_processing_permission,
)

__all__ = ['PinterestClient', 'PinterestError', 'collect_board', 'download_image',
           'validate_image_url', 'validate_processing_permission', 'main']

if __name__ == '__main__':
    raise SystemExit(main())
