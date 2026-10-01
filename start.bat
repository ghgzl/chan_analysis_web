@echo off
chcp 65001 >nul
title 缠论分析网页版 - 一键启动
cd /d "%~dp0"

echo ==========================================
echo    缠论分析网页版 - 一键启动
echo ==========================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python！
    echo.
    echo 请先到 https://www.python.org/downloads/ 下载并安装 Python 3.10 或更高版本
    echo 安装时【务必】勾选 "Add Python to PATH" 这一项
    echo.
    pause
    exit /b 1
)

echo [1/3] 检查依赖是否已安装...
pip show czsc >nul 2>nul
if %errorlevel% neq 0 (
    echo [2/3] 首次运行，正在安装依赖（约需几分钟，请耐心等待）...
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo [错误] 依赖安装失败，请检查网络后重新双击本脚本
        echo 如提示版本冲突，可先运行: pip install -U scipy scikit-learn numpy
        echo.
        pause
        exit /b 1
    )
) else (
    echo [2/3] 依赖已就绪
)

echo [3/3] 正在启动网页应用...
echo.
echo 浏览器将自动打开 http://localhost:8501
echo 关闭本窗口即可停止程序
echo.
start http://localhost:8501
python -m streamlit run streamlit_app.py
pause
