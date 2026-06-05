#!/usr/bin/env python3
"""Markdown 文档格式校验与自动修复工具（向后兼容入口）。"""

from wx_obsidian.output.validator import main, validate_and_fix, validate_file

__all__ = ["validate_and_fix", "validate_file", "main"]

if __name__ == "__main__":
    main()
