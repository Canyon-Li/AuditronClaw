import os
import re
import time
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from functools import lru_cache

from .tools.sandbox_tools import run_office_command

# 技能工具的 StructuredTool.metadata 键:装配点(审批门)据此判定该工具
# 走"技能来源"分级路径。键名单一事实源,执行器与分级器共用。
SKILL_FOLDER_META_KEY = "skill_folder"


def expand_skill_command(command: str, folder: str) -> str:
    """{baseDir} 占位符 → skills/<folder> 实际路径(单一事实源)。

    懒执行器(真正执行)与审批门分级器(判定要不要批)必须用同一个替换,
    否则"批的是这条命令、跑的是另一条"——技能执行与分级同源的命脉。
    """
    return command.replace("{baseDir}", f"skills/{folder}")


class DynamicSkillInput(BaseModel):
    mode: str = Field(
        description="必须是 'help' 或 'run'。第一次使用时强烈建议先传入 'help' 阅读说明书。"
    )
    command: Optional[str] = Field(
        default="",
        description="仅在 mode='run' 时需要。你要执行的完整命令，保留 {baseDir} 占位符。"
    )


class LazySkillLoader:
    """
    渐进式技能加载器 + 缓存机制
    
    特性：
    1. 启动时只扫描元数据（name, description），不加载完整内容
    2. 首次调用技能时才加载完整内容并缓存
    3. 支持热更新（修改技能文件后自动重新加载）
    4. LRU缓存策略，自动清理不常用的技能
    """
    
    def __init__(self, skills_dir: str, office_dir: str, cache_size: int = 50):
        """
        Args:
            skills_dir: 技能目录（office/skills），装配期入参
            office_dir: office 工位根，mode=run 的命令在此执行（与
                execute_office_shell 同一执行体、同一道边界）
            cache_size: 内容缓存量
        """
        self._skills_dir = skills_dir
        self._office_dir = office_dir
        self._skill_registry: Optional[List[Dict[str, Any]]] = None
        self._cache_size = cache_size
        self._last_scan_time = 0.0
        self._scan_interval = 60  # 缓存元数据扫描结果60秒
    
    @lru_cache(maxsize=50)
    def _load_skill_content(self, md_path: str, mtime: float) -> str:
        """
        加载技能完整内容（带缓存）
        
        Args:
            md_path: 技能文件路径
            mtime: 文件修改时间（用于缓存失效检测）
        
        Returns:
            技能的完整 Markdown 内容
        """
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read()
    
    def _scan_skills(self, force_rescan: bool = False) -> List[Dict[str, Any]]:
        """
        扫描技能目录，只提取元数据（轻量级操作）
        
        Args:
            force_rescan: 是否强制重新扫描（忽略缓存）
        
        Returns:
            技能元数据列表
        """
        current_time = time.time()
        
        # 缓存检查：如果最近扫描过且不强制刷新，直接返回缓存
        if (not force_rescan and 
            self._skill_registry is not None and 
            current_time - self._last_scan_time < self._scan_interval):
            return self._skill_registry
        
        skills = []
        
        if not os.path.exists(self._skills_dir):
            self._skill_registry = []
            self._last_scan_time = current_time
            return []

        for item in os.listdir(self._skills_dir):
            folder_path = os.path.join(self._skills_dir, item)
            if not os.path.isdir(folder_path):
                continue
            
            md_path = os.path.join(folder_path, "SKILL.md")
            if not os.path.exists(md_path):
                md_path = os.path.join(folder_path, "README.md")
            
            if not os.path.exists(md_path):
                continue
            
            try:
                # 只读取前几行（name, description）
                metadata = self._extract_metadata(md_path)
                
                if metadata:
                    skills.append({
                        "folder": item,
                        "md_path": md_path,
                        "mtime": os.path.getmtime(md_path),
                        **metadata
                    })
            except Exception as e:
                print(f" [警告] 扫描技能 {item} 失败: {e}")
        
        self._skill_registry = skills
        self._last_scan_time = current_time
        
        if skills:
            print(f" [OK] 扫描到 {len(skills)} 个技能（懒加载模式）")
        
        return skills
    
    def _extract_metadata(self, md_path: str) -> Optional[Dict[str, str]]:
        """
        从技能文件中提取元数据（只读取必要的部分）
        
        Args:
            md_path: 技能文件路径
        
        Returns:
            包含 name 和 description 的字典
        """
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                # 只读取前 50 行（通常元数据在文件开头）
                lines = []
                for i, line in enumerate(f):
                    if i >= 50:
                        break
                    lines.append(line)
                
                content = "\n".join(lines)
            
            name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            
            raw_name = name_match.group(1).strip() if name_match else os.path.basename(os.path.dirname(md_path))
            tool_name = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_name)
            
            raw_desc = desc_match.group(1).strip() if desc_match else f"提供 {raw_name} 相关功能"
            if (raw_desc.startswith('"') and raw_desc.endswith('"')) or (raw_desc.startswith("'") and raw_desc.endswith("'")):
                raw_desc = raw_desc[1:-1]
            
            return {
                "raw_name": raw_name,
                "name": tool_name,
                "description": raw_desc
            }
        except Exception as e:
            print(f" [警告] 提取元数据失败 {md_path}: {e}")
            return None
    
    def _create_lazy_tool(self, skill_info: Dict[str, Any]) -> StructuredTool:
        """
        创建懒加载工具对象
        
        Args:
            skill_info: 技能元数据
        
        Returns:
            LangChain 工具对象
        """
        def lazy_runner(mode: str, command: str = "") -> str:
            """懒加载执行器：首次调用时才加载完整内容"""
            if mode == "help":
                # 懒加载：首次调用时才读取完整内容
                skill_content = self._load_skill_content(
                    skill_info["md_path"], 
                    skill_info["mtime"]
                )
                
                return (
                    f"========== 【{skill_info['raw_name']} 完整说明书】 ==========\n"
                    f"{skill_content[:3000]}\n"
                    f"====================================\n"
                    f"提示：请根据以上说明，如果觉得能解决问题，就将 mode 设为 'run'，"
                    f"并将拼装好的执行命令填入 command 重新调用。"
                )
            elif mode == "run":
                if not command:
                    return "错误：在 'run' 模式下，必须提供 command 参数！"

                actual_cmd = expand_skill_command(command, skill_info["folder"])
                return run_office_command(self._office_dir, actual_cmd)
            else:
                return "错误：mode 参数只能是 'help' 或 'run'。"
        
        mini_description = (
            f"{skill_info['description']}\n\n"
            f"注意：这是一个外部扩展技能。首次使用请务必先传入 `mode='help'` 来阅读完整说明书，"
            f"之后再使用 `mode='run'` 配合 `command` 执行底层脚本。"
        )
        
        return StructuredTool.from_function(
            func=lazy_runner,
            name=skill_info["name"],
            description=mini_description,
            args_schema=DynamicSkillInput,
            # 装配点判"技能工具"的依据:审批门据此按其命令做段级分级
            # (mode=run 最终交给 execute_office_shell,收敛同源)
            metadata={SKILL_FOLDER_META_KEY: skill_info["folder"]},
        )
    
    def get_all_tools(self, force_rescan: bool = False) -> List[StructuredTool]:
        """
        获取所有工具（懒加载占位符）
        
        Args:
            force_rescan: 是否强制重新扫描技能目录
        
        Returns:
            工具对象列表
        """
        skill_infos = self._scan_skills(force_rescan=force_rescan)
        
        tools = []
        for skill_info in skill_infos:
            tools.append(self._create_lazy_tool(skill_info))
        
        return tools
    
    def get_tool_count(self) -> int:
        """获取技能数量（不触发加载）"""
        return len(self._scan_skills())
    
    def clear_cache(self):
        """清除所有缓存"""
        self._load_skill_content.cache_clear()
        self._skill_registry = None
        print(" [OK] 技能缓存已清除")


def load_dynamic_skills(skills_dir: str, office_dir: str,
                        force_rescan: bool = False) -> List[StructuredTool]:
    """
    装配动态技能（懒加载工具占位符）：目录为装配期入参（05 票）。

    每次装配独立构造加载器——没有模块级落点，工作区路径由调用方
    （入口/测试/基准）注入；技能命令的执行体与 execute_office_shell
    同源（run_office_command），同一套白名单与回执格式。

    Args:
        skills_dir: 技能目录（office/skills）
        office_dir: office 工位根（mode=run 的执行目录）
        force_rescan: 是否强制重新扫描技能目录（默认 False）

    Returns:
        工具对象列表（懒加载占位符）
    """
    return LazySkillLoader(skills_dir, office_dir).get_all_tools(
        force_rescan=force_rescan)