from typing import List, Optional, Union

CLIENT_ID: List[int] = [
    17, 19, 4, 33, 4, 33, 45, 19, 21, 20, 116, 44, 116, 113, 10, 4, 20, 21, 41, 119, 43, 111,
    43, 4, 23, 44, 26, 54, 18, 22, 29, 32, 47, 38, 24, 119, 51, 54, 0, 36, 48, 48, 41
]


def deobfuscate(data: Optional[Union[List[int], str]]) -> str:
    """Deobfuscate a key stored in this module, or empty for anything not a list."""
    if not data or not isinstance(data, list):
        return ""
    return "".join(chr(b ^ 0x42) for b in data)


def obfuscate(data: str) -> List[int]:
    """Obfuscate a key for storing in this module."""
    if not data:
        return []
    return [ord(c) ^ 0x42 for c in data]
