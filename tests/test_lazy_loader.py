"""
测试懒加载技能加载器（05 票起实例化 API：skills_dir/office_dir 装配入参，
无模块级落点、无 reload 链）
"""
import os
import sys
import time
import tempfile
import shutil

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auditronclaw.core.skill_loader import LazySkillLoader, load_dynamic_skills


def create_test_skills(test_dir: str, num_skills: int = 5):
    """创建测试技能"""
    skills_dir = os.path.join(test_dir, "office", "skills")
    os.makedirs(skills_dir, exist_ok=True)

    for i in range(num_skills):
        skill_dir = os.path.join(skills_dir, f"test_skill_{i}")
        os.makedirs(skill_dir, exist_ok=True)

        skill_content = f"""name: Test Skill {i}
description: 这是第 {i} 个测试技能，用于验证懒加载机制

## 详细说明

这是一个测试技能的详细文档内容。
它应该有足够的内容来测试缓存机制。

## 使用方法

1. 先调用 mode='help' 查看此文档
2. 然后调用 mode='run' 执行命令

命令示例：
```bash
echo "Skill {i} executed"
```
"""

        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_content)

    return skills_dir


def test_lazy_loading():
    """测试懒加载功能"""
    print("=" * 60)
    print("测试 1: 基本懒加载功能")
    print("=" * 60)

    # 创建临时测试目录,直接装配加载器(目录入参,不碰环境与模块状态)
    temp_dir = tempfile.mkdtemp(prefix="auditronclaw_test_")
    skills_dir = create_test_skills(temp_dir, num_skills=5)
    office_dir = os.path.join(temp_dir, "office")
    loader = LazySkillLoader(skills_dir, office_dir)

    try:
        # 测试 1: 扫描技能
        print(f"\n[测试 1.1] 扫描技能目录...")
        count = loader.get_tool_count()
        print(f"[OK] 扫描到 {count} 个技能")
        assert count == 5, f"期望 5 个技能，实际 {count}"

        # 测试 2: 获取工具（懒加载占位符）
        print(f"\n[测试 1.2] 获取工具列表（懒加载）...")
        start_time = time.time()
        tools = loader.get_all_tools()
        elapsed = time.time() - start_time
        print(f"[OK] 获取 {len(tools)} 个工具，耗时: {elapsed:.4f}秒")
        assert len(tools) == 5, f"期望 5 个工具，实际 {len(tools)}"

        # 测试 3: 验证工具属性
        print(f"\n[测试 1.3] 验证工具属性...")
        for i, tool in enumerate(tools):
            print(f"  - 工具 {i}: {tool.name}")
            assert "lazy_runner" in str(tool.func), f"工具 {tool.name} 不是懒加载函数"
        print(f"[OK] 所有工具都是懒加载模式")

        # 测试 4: 模拟首次调用（触发完整加载）
        # 注意:os.listdir 顺序由文件系统决定(NTFS 字母序,ext4 哈希序),
        # 不能假设 tools[0] 是 test_skill_0,必须按工具名查找
        print(f"\n[测试 1.4] 模拟首次调用技能（触发完整内容加载）...")
        tool_0 = next(t for t in tools if t.name == "Test_Skill_0")
        start_time = time.time()
        result = tool_0.func(mode='help')
        elapsed = time.time() - start_time
        print(f"[OK] 首次调用耗时: {elapsed:.4f}秒")
        print(f"[OK] 结果预览: {result[:100]}...")
        assert "Test Skill 0" in result, "技能内容未正确加载"

        # 测试 5: 第二次调用（应该使用缓存）
        print(f"\n[测试 1.5] 第二次调用（应该使用缓存）...")
        start_time = time.time()
        result2 = tool_0.func(mode='help')
        elapsed2 = time.time() - start_time
        print(f"[OK] 第二次调用耗时: {elapsed2:.4f}秒")
        if elapsed2 > 0:
            print(f"[OK] 速度提升: {(elapsed / elapsed2):.2f}x")
        else:
            print(f"[OK] 速度提升: 缓存响应极快 (< 0.001s)")
        assert elapsed2 <= elapsed, "第二次调用应该更快或相等（使用缓存）"

        print("\n" + "=" * 60)
        print("测试 2: 强制重新扫描")
        print("=" * 60)

        # 测试 6: 添加新技能
        print(f"\n[测试 2.1] 添加新技能...")
        new_skill_dir = os.path.join(skills_dir, "new_skill")
        os.makedirs(new_skill_dir, exist_ok=True)
        with open(os.path.join(new_skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("name: New Skill\ndescription: 新添加的技能")

        # 强制重新扫描
        print(f"\n[测试 2.2] 强制重新扫描...")
        tools_after = loader.get_all_tools(force_rescan=True)
        print(f"[OK] 扫描后技能数: {len(tools_after)}")
        assert len(tools_after) == 6, f"期望 6 个技能，实际 {len(tools_after)}"

        print("\n" + "=" * 60)
        print("测试 3: 缓存清除")
        print("=" * 60)

        # 测试 7: 清除缓存
        print(f"\n[测试 3.1] 清除缓存...")
        loader.clear_cache()

        # 再次调用应该重新加载
        print(f"\n[测试 3.2] 缓存清除后首次调用...")
        start_time = time.time()
        result3 = tool_0.func(mode='help')
        elapsed3 = time.time() - start_time
        print(f"[OK] 缓存清除后调用耗时: {elapsed3:.4f}秒")

        print("\n" + "=" * 60)
        print("[PASS] 所有测试通过！")
        print("=" * 60)

    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n[OK] 临时测试目录已清理")


def test_load_dynamic_skills_assembles_from_given_dirs():
    """模块级装配入口:吃 skills_dir/office_dir 入参,返回懒加载占位符"""
    temp_dir = tempfile.mkdtemp(prefix="auditronclaw_assemble_")
    try:
        skills_dir = create_test_skills(temp_dir, num_skills=2)
        office_dir = os.path.join(temp_dir, "office")
        tools = load_dynamic_skills(skills_dir, office_dir)
        assert len(tools) == 2
        assert all("lazy_runner" in str(t.func) for t in tools)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_lazy_loading()
    test_load_dynamic_skills_assembles_from_given_dirs()
