# tools 子包:内置工具与沙盒工具。
# 该 __init__.py 同时是打包必需的——缺了它 find_packages() 不会收录本目录,
# pip 安装后 from auditronclaw.core.tools import ... 会 ModuleNotFoundError。
