"""PlatformIO post-build step: one image for flashing a blank board from the browser.

Writes .pio/build/<env>/<env>-<version>-factory.bin = bootloader + partition table + boot_app0 +
the app, merged at their offsets, so Fullscope Office's flasher writes a single file at 0x0.
Only the USB builds (outdoor, indoor) make one; the OTA builds upload the app alone.
"""
import re

Import("env")  # noqa: F821  (PlatformIO SCons global)


def version():
    text = open(env.subst("$PROJECT_INCLUDE_DIR/common.h")).read()  # noqa: F821
    m = re.search(r'#define\s+FW_VERSION\s+"([^"]+)"', text)
    return m.group(1) if m else "dev"


def merge(source, target, env):
    build = env.subst("$BUILD_DIR")
    name = env.subst("$PIOENV")
    out = f"{build}/{name}-{version()}-factory.bin"
    parts = []
    for offset, image in env.get("FLASH_EXTRA_IMAGES", []):
        parts += [offset, env.subst(image)]
    parts += [env.subst("$ESP32_APP_OFFSET"), f"{build}/{env.subst('$PROGNAME')}.bin"]
    mode = env.BoardConfig().get("build.flash_mode", "dio")
    cmd = ['"$PYTHONEXE"', '"$OBJCOPY"', "--chip", "esp32", "merge_bin", "-o", f'"{out}"',
           "--flash_mode", mode, "--flash_freq", "40m", "--flash_size", "4MB"] + [f'"{p}"' for p in parts]
    env.Execute(" ".join(cmd))
    print(f"Factory image for Fullscope Office: {out}")


if not env.subst("$PIOENV").endswith("_ota"):  # noqa: F821
    env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", merge)  # noqa: F821
