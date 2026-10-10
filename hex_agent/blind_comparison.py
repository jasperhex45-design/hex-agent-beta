"""Public compatibility entry for a local blind candidate comparison."""
from liu_agent.blind_comparison import *  # noqa: F401,F403

if __name__ == '__main__':
    from liu_agent.blind_comparison import main
    raise SystemExit(main())
