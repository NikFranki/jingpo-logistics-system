#!/usr/bin/env python3
"""生成一个 UUID v4，可用作接口的 Idempotency-Key。"""

from uuid import uuid4


if __name__ == "__main__":
    print(uuid4(), end="")
