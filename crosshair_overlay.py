#!/usr/bin/env python3
"""macOS center-line overlay for browser FPS games."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class OverlayConfig:
    opacity: float
    thickness: float
    rgb: tuple[int, int, int]
    offset_x: float
    offset_y: float


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


def _opacity_value(value: str) -> float:
    parsed = float(value)
    if parsed < 0 or parsed > 1:
        raise argparse.ArgumentTypeError("opacity must be between 0 and 1")
    return parsed


def _parse_color(value: str) -> tuple[int, int, int]:
    cleaned = value.strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:]
    if len(cleaned) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in cleaned):
        return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))

    parts = [part.strip() for part in value.split(",")]
    if len(parts) == 3:
        try:
            rgb = tuple(int(part) for part in parts)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("RGB values must be integers") from exc
        if all(0 <= component <= 255 for component in rgb):
            return rgb

    raise argparse.ArgumentTypeError(
        "color must be #RRGGBB or R,G,B (0-255)"
    )


def parse_args(argv: list[str]) -> OverlayConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Display transparent horizontal and vertical center lines "
            "inside the active Safari window on macOS."
        )
    )
    parser.add_argument(
        "--opacity",
        type=_opacity_value,
        default=0.25,
        help="Line opacity from 0.0 to 1.0 (default: 0.25).",
    )
    parser.add_argument(
        "--thickness",
        type=_positive_float,
        default=1.5,
        help="Line thickness in points (default: 1.5).",
    )
    parser.add_argument(
        "--color",
        type=_parse_color,
        default=(0, 255, 140),
        help="Line color as #RRGGBB or R,G,B (default: 0,255,140).",
    )
    parser.add_argument(
        "--offset-x",
        type=float,
        default=0.0,
        help="Horizontal offset in points; positive moves right (default: 0).",
    )
    parser.add_argument(
        "--offset-y",
        type=float,
        default=65.0,
        help="Vertical offset in points; positive moves down (default: 65).",
    )
    args = parser.parse_args(argv)
    return OverlayConfig(
        opacity=args.opacity,
        thickness=args.thickness,
        rgb=args.color,
        offset_x=args.offset_x,
        offset_y=args.offset_y,
    )


def run_overlay(config: OverlayConfig) -> int:
    try:
        import CoreFoundation
        import Quartz
        import objc
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSBackingStoreBuffered,
            NSBezierPath,
            NSColor,
            NSMakeRect,
            NSMenu,
            NSMenuItem,
            NSNotificationCenter,
            NSScreen,
            NSStatusBar,
            NSVariableStatusItemLength,
            NSWindow,
            NSWorkspace,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSWindowStyleMaskBorderless,
        )
        from Foundation import NSObject, NSTimer
    except ImportError:
        print(
            "Missing dependency. Install with:\n"
            "  pip3 install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    overlay_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
        config.rgb[0] / 255.0,
        config.rgb[1] / 255.0,
        config.rgb[2] / 255.0,
        config.opacity,
    )
    jump_toggle_keycode = 6
    left_shift_keycode = 56
    space_keycode = 49
    jump_shift_delay_seconds = 0.012
    jump_key_press_seconds = 0.018
    jump_repeat_interval_seconds = 0.095

    class CrosshairView(objc.lookUpClass("NSView")):
        line_color = overlay_color
        line_thickness = config.thickness
        x_offset = config.offset_x
        y_offset = config.offset_y

        def initWithFrame_color_thickness_xOffset_yOffset_(
            self, frame, line_color, thickness, x_offset, y_offset
        ):
            self = objc.super(CrosshairView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.line_color = line_color
            self.line_thickness = thickness
            self.x_offset = x_offset
            self.y_offset = y_offset
            return self

        def isOpaque(self):
            return False

        def drawRect_(self, _dirty_rect):
            bounds = self.bounds()
            center_x = bounds.size.width / 2.0 + self.x_offset
            center_y = bounds.size.height / 2.0 - self.y_offset
            half = self.line_thickness / 2.0

            self.line_color.set()
            NSBezierPath.fillRect_(
                NSMakeRect(center_x - half, 0.0, self.line_thickness, bounds.size.height)
            )
            NSBezierPath.fillRect_(
                NSMakeRect(0.0, center_y - half, bounds.size.width, self.line_thickness)
            )

    class OverlayController(NSObject):
        def initWithConfig_(self, cfg):
            self = objc.super(OverlayController, self).init()
            if self is None:
                return None
            self.config = cfg
            self.status_item = None
            self.window = None
            self.view = None
            self.refresh_timer = None
            self.jump_event_tap = None
            self.jump_event_callback = None
            self.jump_event_source = None
            self.jump_repeat_timer = None
            self.jump_repeat_enabled = False
            return self

        def applicationDidFinishLaunching_(self, _notification):
            self._create_status_item()
            self._create_overlay_window()
            self._create_jump_event_tap()
            self.refreshOverlay_(None)
            self.refresh_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.12,
                self,
                "refreshOverlay:",
                None,
                True,
            )
            NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self,
                "screenConfigChanged:",
                "NSApplicationDidChangeScreenParametersNotification",
                None,
            )

        def applicationWillTerminate_(self, _notification):
            if self.refresh_timer is not None:
                self.refresh_timer.invalidate()
                self.refresh_timer = None
            self._stop_jump_repeat()
            if self.jump_event_tap is not None:
                Quartz.CGEventTapEnable(self.jump_event_tap, False)
                self.jump_event_tap = None
                self.jump_event_callback = None
                self.jump_event_source = None

        def screenConfigChanged_(self, _notification):
            self.refreshOverlay_(None)

        def quitOverlay_(self, _sender):
            NSApplication.sharedApplication().terminate_(None)

        def handleJumpEvent_type_event_(self, _proxy, event_type, event):
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                if self.jump_event_tap is not None:
                    Quartz.CGEventTapEnable(self.jump_event_tap, True)
                return event

            if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event,
                Quartz.kCGKeyboardEventKeycode,
            )
            if int(keycode) != jump_toggle_keycode:
                return event

            if self._frontmost_safari_pid() is None:
                return event

            if event_type == Quartz.kCGEventKeyUp:
                return None

            is_repeat = bool(
                Quartz.CGEventGetIntegerValueField(
                    event,
                    Quartz.kCGKeyboardEventAutorepeat,
                )
            )
            if is_repeat:
                return None

            if self.jump_repeat_enabled:
                self._disable_jump_repeat()
            else:
                self.jump_repeat_enabled = True
                self._emit_jump_sequence()
                self._start_jump_repeat()
            return None

        def _window_level(self):
            key = getattr(Quartz, "kCGScreenSaverWindowLevelKey", None)
            if key is None:
                return 1000
            return Quartz.CGWindowLevelForKey(key)

        def _create_overlay_window(self):
            behavior = (
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
            )
            frame = NSMakeRect(0.0, 0.0, 100.0, 100.0)
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                frame,
                NSWindowStyleMaskBorderless,
                NSBackingStoreBuffered,
                False,
            )
            self.window.setLevel_(self._window_level())
            self.window.setOpaque_(False)
            self.window.setBackgroundColor_(NSColor.clearColor())
            self.window.setHasShadow_(False)
            self.window.setIgnoresMouseEvents_(True)
            self.window.setCollectionBehavior_(behavior)
            self.window.setReleasedWhenClosed_(False)

            self.view = CrosshairView.alloc().initWithFrame_color_thickness_xOffset_yOffset_(
                NSMakeRect(0.0, 0.0, frame.size.width, frame.size.height),
                overlay_color,
                self.config.thickness,
                self.config.offset_x,
                self.config.offset_y,
            )
            self.window.setContentView_(self.view)
            self.window.orderOut_(None)

        def _create_jump_event_tap(self):
            event_mask = (
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            )

            def callback(proxy, event_type, event, _refcon):
                return self.handleJumpEvent_type_event_(proxy, event_type, event)

            self.jump_event_callback = callback
            self.jump_event_tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                event_mask,
                self.jump_event_callback,
                None,
            )
            if self.jump_event_tap is None:
                print(
                    "Could not install keyboard event tap. Grant Accessibility "
                    "permission to your terminal/python host, then restart.",
                    file=sys.stderr,
                )
                return

            self.jump_event_source = CoreFoundation.CFMachPortCreateRunLoopSource(
                None,
                self.jump_event_tap,
                0,
            )
            CoreFoundation.CFRunLoopAddSource(
                CoreFoundation.CFRunLoopGetCurrent(),
                self.jump_event_source,
                CoreFoundation.kCFRunLoopCommonModes,
            )
            Quartz.CGEventTapEnable(self.jump_event_tap, True)

        def _start_jump_repeat(self):
            if self.jump_repeat_timer is not None:
                return
            self.jump_repeat_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                jump_repeat_interval_seconds,
                self,
                "repeatJump:",
                None,
                True,
            )

        def _stop_jump_repeat(self):
            if self.jump_repeat_timer is not None:
                self.jump_repeat_timer.invalidate()
                self.jump_repeat_timer = None

        def _disable_jump_repeat(self):
            self.jump_repeat_enabled = False
            self._stop_jump_repeat()

        def repeatJump_(self, _timer):
            if not self.jump_repeat_enabled or self._frontmost_safari_pid() is None:
                self._disable_jump_repeat()
                return
            self._emit_jump_sequence()

        def _post_key(self, keycode, is_down):
            event = Quartz.CGEventCreateKeyboardEvent(None, keycode, is_down)
            if event is None:
                return
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

        def _tap_key(self, keycode):
            self._post_key(keycode, True)
            time.sleep(jump_key_press_seconds)
            self._post_key(keycode, False)

        def _emit_jump_sequence(self):
            self._tap_key(space_keycode)
            time.sleep(jump_shift_delay_seconds)
            self._tap_key(left_shift_keycode)

        def _frontmost_safari_pid(self):
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            bundle_id = app.bundleIdentifier()
            app_name = app.localizedName()
            if bundle_id == "com.apple.Safari" or app_name == "Safari":
                return int(app.processIdentifier())
            return None

        def _front_safari_bounds(self, safari_pid):
            options = (
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements
            )
            window_list = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
            owner_pid_key = Quartz.kCGWindowOwnerPID
            bounds_key = Quartz.kCGWindowBounds
            layer_key = Quartz.kCGWindowLayer
            alpha_key = Quartz.kCGWindowAlpha

            mouse_point = None
            mouse_event = Quartz.CGEventCreate(None)
            if mouse_event is not None:
                point = Quartz.CGEventGetLocation(mouse_event)
                mouse_point = (float(point.x), float(point.y))

            candidates = []
            for window in window_list:
                if int(window.get(owner_pid_key, -1)) != safari_pid:
                    continue
                if int(window.get(layer_key, 1)) != 0:
                    continue
                if float(window.get(alpha_key, 0.0)) <= 0:
                    continue

                bounds = window.get(bounds_key)
                if not bounds:
                    continue

                width = float(bounds.get("Width", 0.0))
                height = float(bounds.get("Height", 0.0))
                if width < 120 or height < 120:
                    continue

                x = float(bounds.get("X", 0.0))
                y = float(bounds.get("Y", 0.0))
                area = width * height
                contains_mouse = False
                if mouse_point is not None:
                    mouse_x, mouse_y = mouse_point
                    contains_mouse = x <= mouse_x <= x + width and y <= mouse_y <= y + height
                candidates.append(
                    {
                        "contains_mouse": 1 if contains_mouse else 0,
                        "area": area,
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                        "bounds": bounds,
                    }
                )

            if not candidates:
                return None

            candidates.sort(
                reverse=True,
                key=lambda item: (item["contains_mouse"], item["area"]),
            )
            primary = candidates[0]

            # Safari often exposes both outer-window and web-content surfaces.
            # Prefer a large inner surface to avoid centering against toolbar chrome.
            inner_candidates = []
            px = primary["x"]
            py = primary["y"]
            pw = primary["width"]
            ph = primary["height"]
            for candidate in candidates[1:]:
                x = candidate["x"]
                y = candidate["y"]
                width = candidate["width"]
                height = candidate["height"]

                if width < pw * 0.94:
                    continue
                if abs(x - px) > max(8.0, pw * 0.02):
                    continue
                if y <= py + 12.0:
                    continue
                if height >= ph - 12.0:
                    continue
                if x + width > px + pw + 8.0:
                    continue
                if y + height > py + ph + 8.0:
                    continue

                score = (candidate["contains_mouse"], candidate["area"])
                inner_candidates.append((score, candidate["bounds"]))

            if inner_candidates:
                inner_candidates.sort(reverse=True, key=lambda item: item[0])
                return inner_candidates[0][1]

            return primary["bounds"]

        def _ns_rect_from_cg_bounds(self, bounds):
            x = float(bounds.get("X", 0.0))
            y = float(bounds.get("Y", 0.0))
            width = float(bounds.get("Width", 0.0))
            height = float(bounds.get("Height", 0.0))

            screens = list(NSScreen.screens())
            if not screens:
                return NSMakeRect(x, y, width, height)

            # CG window bounds use a top-left origin on the primary display.
            # Convert into AppKit's bottom-left origin using the primary top edge.
            primary_screen = screens[0]
            primary_top_edge = float(
                primary_screen.frame().origin.y + primary_screen.frame().size.height
            )
            cocoa_y = primary_top_edge - y - height
            return NSMakeRect(x, cocoa_y, width, height)

        def refreshOverlay_(self, _timer):
            if self.window is None or self.view is None:
                return

            safari_pid = self._frontmost_safari_pid()
            if safari_pid is None:
                self.window.orderOut_(None)
                return

            bounds = self._front_safari_bounds(safari_pid)
            if not bounds:
                self.window.orderOut_(None)
                return

            frame = self._ns_rect_from_cg_bounds(bounds)
            self.window.setFrame_display_(frame, True)
            self.view.setFrame_(NSMakeRect(0.0, 0.0, frame.size.width, frame.size.height))
            self.view.setNeedsDisplay_(True)
            self.window.orderFrontRegardless()

        def _create_status_item(self):
            self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength
            )
            button = self.status_item.button()
            if button is not None:
                button.setTitle_("Crosshair")

            menu = NSMenu.alloc().init()
            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit",
                "quitOverlay:",
                "q",
            )
            quit_item.setTarget_(self)
            menu.addItem_(quit_item)
            self.status_item.setMenu_(menu)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    controller = OverlayController.alloc().initWithConfig_(config)
    app.setDelegate_(controller)

    def _handle_interrupt(_signum, _frame):
        NSApplication.sharedApplication().terminate_(None)

    signal.signal(signal.SIGINT, _handle_interrupt)

    print(
        "Crosshair overlay running. Use the menu-bar 'Crosshair' item and click Quit to stop."
    )
    app.run()
    return 0


def main(argv: list[str]) -> int:
    config = parse_args(argv)
    return run_overlay(config)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
