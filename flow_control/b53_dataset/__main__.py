"""允许 ``python -m flow_control.b53_dataset`` 直接运行。"""

from .builder import main


if __name__ == "__main__":
    raise SystemExit(main())
