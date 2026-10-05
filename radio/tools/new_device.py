"""Make a LoRa device id and key for one node, and print the lines for its config.h and for the
gateway's (radio/lora/include/config.h, or the thermostat's with LORA_GATEWAY 1).

    python radio/tools/new_device.py outdoor
    python radio/tools/new_device.py indoor

Each node gets its own key: a stolen key only speaks for that node. Keep the output private
(the config.h files are git-ignored) and don't reuse it for another node.
"""
import secrets
import sys


def main():
    node = sys.argv[1] if len(sys.argv) > 1 else "outdoor"
    if node not in ("outdoor", "indoor"):
        sys.exit("node must be outdoor or indoor")
    dev_id = 0
    while dev_id == 0:
        dev_id = secrets.randbits(32)
    key = secrets.token_hex(16)
    print(f"--- hvac-firmware/include/config.h ({node} node)")
    print("#define LORA_ENABLED   1")
    print(f"#define LORA_DEVICE_ID 0x{dev_id:08X}")
    print(f'#define LORA_KEY       "{key}"')
    print("\n--- the gateway's config.h, one line in LORA_DEVICES")
    print(f'    {{0x{dev_id:08X}, "{key}", "{node}"}}, \\')


if __name__ == "__main__":
    main()
