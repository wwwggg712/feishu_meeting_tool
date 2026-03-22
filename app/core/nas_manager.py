import os
import sys
import json
import shutil
from pypinyin import lazy_pinyin
from app.utils.logger import logger

# ========== Windows兼容处理 ==========
# 非Windows系统正常导入pwd，Windows下定义模拟类
if sys.platform != "win32":
    import pwd
else:
    # Windows下模拟pwd模块，避免导入报错
    class pwd:
        @staticmethod
        def getpwuid(uid):
            # 模拟返回值，避免KeyError
            return type('PwdEntry', (object,), {'pw_name': ''})()

class NasManager:
    # 容器映射路径 (对应宿主机 /vol1)
    NAS_ROOT = "/nas_data"
    MAPPING_FILE = "user_token/nas_mapping.json"

    @staticmethod
    def _load_mapping():
        if os.path.exists(NasManager.MAPPING_FILE):
            try:
                with open(NasManager.MAPPING_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}
    
    @staticmethod
    def _find_folder_by_owner_name(target_username):
        """
        遍历 /nas_data 下的所有目录，寻找 owner 是 target_username 的目录
        Windows下自动跳过UID/Owner匹配（无pwd支持），直接返回None
        """
        # Windows系统直接跳过该逻辑（无UID/Owner概念）
        if sys.platform == "win32":
            logger.info("[NAS匹配] Windows系统跳过UID/Owner目录匹配")
            return None
            
        if not os.path.exists(NasManager.NAS_ROOT):
            return None
            
        try:
            # 遍历一级子目录 (仅非Windows系统执行)
            for item in os.listdir(NasManager.NAS_ROOT):
                full_path = os.path.join(NasManager.NAS_ROOT, item)
                if os.path.isdir(full_path):
                    # 获取该目录的 UID
                    stat_info = os.stat(full_path)
                    uid = stat_info.st_uid
                    try:
                        # 通过 UID 反查用户名 (需要挂载 /etc/passwd)
                        owner_name = pwd.getpwuid(uid).pw_name
                        # 忽略大小写进行匹配
                        if owner_name.lower() == target_username.lower():
                            logger.info(f"[NAS匹配] 找到目录: {item} (UID: {uid}, Owner: {owner_name})")
                            return item
                    except KeyError:
                        # 容器内找不到该 UID 对应的用户
                        continue
        except Exception as e:
            logger.error(f"[NAS匹配] 遍历目录查找 owner 失败: {e}")
        return None

    @staticmethod
    def get_nas_folder(user_name, user_id):
        """
        根据用户姓名寻找 NAS 目录 (支持数字目录名+用户名归属匹配)
        Windows下优先使用映射表和目录名匹配，跳过Owner匹配
        优先级:
        1. 手动映射表
        2. 全拼匹配 (Owner Name，仅非Windows)
        3. 英文名匹配 (Owner Name，仅非Windows)
        4. 目录名直接匹配
        """
        if not user_name:
            return None

        # 1. 查映射表 (优先级最高，跨平台通用)
        mapping = NasManager._load_mapping()
        # 1.1 精确匹配 user_id (人工手动配置的优先级最高)
        if user_id in mapping:
            folder = mapping[user_id]
            if os.path.exists(os.path.join(NasManager.NAS_ROOT, folder)):
                return folder

        # 清洗名字
        clean_name = user_name.strip().lower()

        # 拼音转换 (张三 -> zhangsan)
        pinyin_list = lazy_pinyin(clean_name)
        pinyin_name = "".join(pinyin_list).lower()
        
        logger.info(f"[NAS匹配] 正在查找 Owner 为 '{pinyin_name}' 或 '{clean_name}' 的目录...")

        # 1.2 映射表匹配 (自动生成的 Host 用户名映射)
        if pinyin_name in mapping:
            folder = mapping[pinyin_name]
            logger.info(f"[NAS匹配] 映射表由拼音命中: {pinyin_name} -> {folder}")
            return folder
            
        if clean_name in mapping:
            folder = mapping[clean_name]
            logger.info(f"[NAS匹配] 映射表由英文名命中: {clean_name} -> {folder}")
            return folder

        # 2. 尝试按 Owner 查找 (仅非Windows系统执行)
        folder_by_pinyin = NasManager._find_folder_by_owner_name(pinyin_name)
        if folder_by_pinyin:
            return folder_by_pinyin
            
        # 3. 尝试按 Owner 查找 (仅非Windows系统执行)
        if clean_name != pinyin_name:
             folder_by_raw = NasManager._find_folder_by_owner_name(clean_name)
             if folder_by_raw:
                 return folder_by_raw

        # 4. 保底: 目录名直接匹配 (跨平台通用，Windows下核心匹配逻辑)
        try:
            if os.path.exists(NasManager.NAS_ROOT):
                for item in os.listdir(NasManager.NAS_ROOT):
                    # 匹配拼音 (zhangsan -> ZHANGSAN) 或 英文名 (leo -> LEO)
                    if item.lower() == pinyin_name or item.lower() == clean_name:
                         # 再次确认是目录
                         if os.path.isdir(os.path.join(NasManager.NAS_ROOT, item)):
                             logger.info(f"[NAS匹配] 目录名直接匹配成功(忽略大小写): {item}")
                             return item
        except Exception as e:
            logger.warning(f"[NAS匹配] 目录遍历匹配失败: {e}")
        
        return None

    @staticmethod
    def save_to_team_folder(source_file_path, department_names):
        """
        将文件复制到团队文件夹 (跨平台通用)
        :param source_file_path: 源文件路径 (已下载的视频文件)
        :param department_names: 部门名称列表 ["Skyris技术部门", "Skyris管理层"]
        """
        if not department_names:
            return

        file_name = os.path.basename(source_file_path)

        for dept_name in department_names:
            if not dept_name:
                continue
            
            # 团队文件夹路径 (在 @team 子目录下)
            team_folder_path = os.path.join(NasManager.NAS_ROOT, "@team", dept_name)
            
            # 检查团队文件夹是否存在
            if os.path.exists(team_folder_path) and os.path.isdir(team_folder_path):
                target_file_path = os.path.join(team_folder_path, file_name)
                try:
                    shutil.copy2(source_file_path, target_file_path)
                    logger.info(f"[NAS团队归档] 成功复制文件到: {target_file_path}")
                except Exception as e:
                    logger.error(f"[NAS团队归档] 复制失败 {dept_name}: {e}")
            else:
                logger.debug(f"[NAS团队归档] 忽略: 团队文件夹不存在 ({dept_name})")

    @staticmethod
    def archive_file(local_file_path, user_name, user_id):
        """
        将文件归档到 NAS (跨平台通用)
        返回: (是否成功, 最终路径, 匹配到的文件夹名)
        """
        folder_name = NasManager.get_nas_folder(user_name, user_id)
        
        if not folder_name:
            logger.warning(f"[NAS归档] 未找到用户 {user_name} ({user_id}) 的NAS目录")
            return False, local_file_path, None

        try:
            filename = os.path.basename(local_file_path)
            # 目标: /nas_data/zhangsan/filename.mp4
            nas_path = os.path.join(NasManager.NAS_ROOT, folder_name, filename)
            
            # 移动文件
            shutil.move(local_file_path, nas_path)
            
            # 修改权限 (Windows下跳过chmod，无意义)
            if sys.platform != "win32":
                try:
                    os.chmod(nas_path, 0o666)
                except Exception as e:
                    logger.warning(f"修改文件权限失败: {e}")

            logger.info(f"[NAS归档] 成功移动文件: {local_file_path} -> {nas_path}")
            return True, nas_path, folder_name
            
        except Exception as e:
            logger.error(f"[NAS归档] 移动失败: {e}")
            return False, local_file_path, None