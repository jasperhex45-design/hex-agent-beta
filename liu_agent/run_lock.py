"""Local run ownership. Recovery removes only a proven abandoned lock.

All cooperating writers serialize metadata changes through a permanent OS-locked
guard file. Never remove that guard: replacing its inode would split the mutex.
Recovery does not inspect or mutate requests, results, or budget reservations.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
import uuid

from .io import AgentError


LOCK_FORMAT = 'liu.dispatch-lock.v1'


def _linux_procfs_in_pid_namespace() -> bool:
    """Numeric proc paths are usable only in this process's PID namespace.

    A container may inherit a proc mount from an outer namespace. In that case
    /proc/<getpid()> can describe an unrelated process, including a dead one.
    Do not use its state or start time as evidence about a local lock owner.
    """
    try:
        stat = Path('/proc/self/stat').read_text(encoding='ascii')
        return int(stat.split(' ', 1)[0]) == os.getpid()
    except (OSError, ValueError, UnicodeError):
        return False


def _process_state(pid: int) -> tuple[str, str | None]:
    """Return alive/dead/unknown plus an OS process-start identity, if available."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # ERROR_INVALID_PARAMETER is returned when this PID no longer exists.
            return ('dead', None) if ctypes.get_last_error() == 87 else ('unknown', None)
        try:
            creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in
                    (creation, exit_time, kernel_time, user_time))):
                return 'unknown', None
            identity = f'windows-filetime:{(creation.dwHighDateTime << 32) | creation.dwLowDateTime}'
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return 'unknown', None
            # Exit time is nonzero after exit, including an exit code of STILL_ACTIVE.
            exited = bool(exit_time.dwHighDateTime or exit_time.dwLowDateTime) or code.value != 259
            return ('dead' if exited else 'alive'), identity
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith('linux') and _linux_procfs_in_pid_namespace():
        try:
            stat = Path(f'/proc/{pid}/stat').read_text(encoding='ascii')
            fields = stat[stat.rfind(')') + 2:].split()
            boot = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
            identity = f'linux-start:{boot}:{fields[19]}'
            return ('dead' if fields[0] in {'Z', 'X'} else 'alive'), identity
        except FileNotFoundError:
            pass  # A missing proc filesystem is not proof that the owner exited.
        except (OSError, ValueError, IndexError, UnicodeError):
            return 'unknown', None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return 'dead', None
    except (OSError, OverflowError):
        return 'unknown', None
    # Unsupported or mismatched proc views cannot prove a process-start
    # identity. kill(pid, 0) addresses only this process's PID namespace.
    return 'alive', None


@contextmanager
def _metadata_guard(run: Path):
    """Short, nonblocking OS mutex; automatically released after a crash."""
    with (run / '.dispatch.guard').open('a+b', buffering=0) as guard:
        guard.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                # Windows allows locking a byte beyond EOF; no initialization race.
                msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise AgentError('任务锁正在被其他进程检查或写入；稍后重试') from exc
            try:
                yield
            finally:
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise AgentError('任务锁正在被其他进程检查或写入；稍后重试') from exc
            try:
                yield
            finally:
                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


def _parse_owner(raw: bytes) -> dict | None:
    try:
        owner = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if (not isinstance(owner, dict) or owner.get('format') != LOCK_FORMAT
            or type(owner.get('pid')) is not int or not 0 < owner['pid'] <= 0xFFFFFFFF
            or not isinstance(owner.get('process_start_identity'), str)
            or not owner['process_start_identity']
            or not isinstance(owner.get('nonce'), str) or not owner['nonce']):
        return None
    return owner


def _comparable_identity(recorded: str, observed: str | None) -> bool:
    """Unknown or foreign identity formats cannot prove that a PID was reused."""
    if not observed:
        return False
    for pattern in (r'windows-filetime:[0-9]+',
                    r'linux-start:[0-9a-fA-F-]+:[0-9]+'):
        if re.fullmatch(pattern, recorded) and re.fullmatch(pattern, observed):
            return True
    return False


@contextmanager
def dispatch_lock(run: Path):
    """Acquire exclusively; never automatically reclaim an existing lock."""
    run = Path(run)
    path = run / '.dispatch.lock'
    state, identity = _process_state(os.getpid())
    owner = {'format': LOCK_FORMAT, 'pid': os.getpid(),
             'process_start_identity': identity if state == 'alive' and identity else 'unavailable',
             'nonce': uuid.uuid4().hex}
    with _metadata_guard(run):
        try:
            fd = path.open('x', encoding='utf-8')
        except FileExistsError as exc:
            raise AgentError('此任务有并发写入或中断锁；先检查原进程，不重复生图') from exc
        # If serialization/flush/fsync fails, leave the file for conservative inspection.
        with fd:
            json.dump(owner, fd, ensure_ascii=True, sort_keys=True)
            fd.flush()
            os.fsync(fd.fileno())
    try:
        yield
    finally:
        with _metadata_guard(run):
            try:
                current = _parse_owner(path.read_bytes())
            except FileNotFoundError:
                current = None
            if current == owner:
                path.unlink()


def recover_lock(run: Path) -> dict:
    """Explicitly remove a verifiably dead owner's lock, without replaying work."""
    run = Path(run)
    path = run / '.dispatch.lock'
    try:
        with _metadata_guard(run):
            try:
                original = path.read_bytes()
            except FileNotFoundError:
                return {'status': 'no_lock', 'reason': 'lock_absent'}
            owner = _parse_owner(original)
            if owner is None:
                return {'status': 'blocked', 'reason': 'legacy_or_invalid_lock'}
            state, identity = _process_state(owner['pid'])
            if state == 'unknown':
                return {'status': 'blocked', 'reason': 'owner_state_unknown', 'owner': owner}
            if state != 'dead':
                if not _comparable_identity(owner['process_start_identity'], identity):
                    return {'status': 'blocked', 'reason': 'owner_identity_unknown', 'owner': owner}
                if identity == owner['process_start_identity']:
                    return {'status': 'blocked', 'reason': 'owner_alive', 'owner': owner}
            # A different OS start identity proves the recorded owner exited (PID reuse).
            reason = 'owner_exited' if state == 'dead' else 'owner_pid_reused'
            try:
                if path.read_bytes() != original:
                    return {'status': 'blocked', 'reason': 'lock_changed'}
                path.unlink()
            except FileNotFoundError:
                return {'status': 'blocked', 'reason': 'lock_changed'}
            return {'status': 'recovered', 'reason': reason, 'owner': owner}
    except (OSError, AgentError) as exc:
        return {'status': 'blocked', 'reason': 'lock_unavailable', 'detail': str(exc)}
