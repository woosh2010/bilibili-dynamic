#!/bin/bash
# 双击此文件启动 B站项目启动面板（浏览器打开 http://localhost:8084）
cd "$(dirname "$0")"
exec python3 launcher.py
