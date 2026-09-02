#!/usr/bin/env python3
"""
一个简单的 Python 脚本，打印 'Hello, World!'
并包含一个加法函数。
"""


def main():
    """主函数，演示脚本的功能。"""
    print("Hello, World!")

    # 演示 add_numbers 函数
    result = add_numbers(5, 3)
    print(f"5 + 3 = {result}")

    # 另一个示例
    result2 = add_numbers(10, 20)
    print(f"10 + 20 = {result2}")


def add_numbers(a: float, b: float) -> float:
    """
    将两个数字相加并返回结果。

    参数:
        a: 第一个数字
        b: 第二个数字

    返回:
        a 和 b 的和
    """
    return a + b


if __name__ == "__main__":
    main()
