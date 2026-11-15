"""테스트가 사용자 화면에 창을 띄우지 않게 막는다.

실제 창 테스트는 SHUTDOWN_SCHEDULER_REAL_WINDOWS=1 일 때만 돈다. 그 밖에는 건너뛴다.
화면이 뜨므로 가상 화면(xvfb-run) 안에서 켜는 것을 권한다.
"""

import os
import tkinter

REAL_WINDOWS_ENV = "SHUTDOWN_SCHEDULER_REAL_WINDOWS"


def _no_real_window(self: tkinter.Tk, *args: object, **kwargs: object) -> None:
    raise tkinter.TclError(f"실제 창 테스트는 {REAL_WINDOWS_ENV}=1 일 때만 돎")


if os.environ.get(REAL_WINDOWS_ENV) != "1":
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)
    # Windows 는 DISPLAY 없이도 창이 뜨므로 생성 자체를 막음
    tkinter.Tk.__init__ = _no_real_window  # type: ignore[method-assign]
