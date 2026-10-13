"""Canonical HEX entry; learning remains an optional, dependency-light command."""
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == 'learning':
        from liu_agent.work_learning_cli import main as learning_main
        return learning_main(args[1:])
    from liu_agent.windows_runtime import main as runtime_main
    return runtime_main(args)


if __name__ == '__main__':
    raise SystemExit(main())
