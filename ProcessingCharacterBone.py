"""
ProcessingCharacter.py v3 — 专注骨骼处理（不碰模型）
==================================================
流程:
  1. 准备: 显示骨骼集合 + 移除约束 + 解锁变换
  2. 记录层级: 保存有顶点权重的骨骼的原始父子关系
  3. 删除无用骨: 仅删除没有任何网格体权重的骨骼
  4. 重命名: 去前缀 → UE标准命名 → 替换特殊字符
  5. 同步 Vertex Group: 将改名同步到所有绑定 Mesh
  6. 重建层级: 创建 root + 恢复父子关系

用法: 选中骨架 → 运行脚本
"""

import bpy
import bmesh
import mathutils
import re

# ═══════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════

STRIP_PREFIXES = ["DEF-f_","DEF-", "ORG-", "MCH-","f_"]

UE_NAME_MAP = {
    "+PelvisTwist CF A01": "pelvis",
    "spine_004": "neck",
    "spine_005": "neck_01",
    "spine_006": "head",
    "forearm": "lowerarm",
    "shoulder": "clavicle",
    "shin": "calf",
    "toe": "ball",
    "EyeBoneA02": "EyeBone",
}

CHAR_TO_REPLACE = "."
CHAR_REPLACEMENT = "_"

# ═══════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════

def _get_armature():
    arm = bpy.context.active_object
    if arm and arm.type == 'ARMATURE':
        return arm
    for obj in bpy.context.scene.objects:
        if obj.type == 'ARMATURE':
            return obj
    raise RuntimeError("未找到骨架对象！")

def _get_bound_meshes(arm):
    meshes = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object == arm:
                meshes.append(obj)
                break
    return meshes

def _enter_object():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

def _get_bones_with_weight(arm, meshes):
    """
    返回所有有顶点权重的骨骼名集合。
    效率: O(总顶点数 × 平均group数)，仅遍历一次所有顶点。
    """
    bones_with_weight = set()
    if not meshes:
        return bones_with_weight

    for mesh_obj in meshes:
        # 快速路径: 跳过无 vertex group 的数据
        if not mesh_obj.vertex_groups:
            continue

        # 建立一个 vertex group 索引 → 名称的映射
        idx_to_name = {}
        for vg in mesh_obj.vertex_groups:
            idx_to_name[vg.index] = vg.name

        # 遍历所有顶点一次，收集有权重 > 0 的 group
        for vert in mesh_obj.data.vertices:
            for g in vert.groups:
                if g.weight > 0:
                    name = idx_to_name.get(g.group)
                    if name:
                        bones_with_weight.add(name)

    return bones_with_weight

# ═══════════════════════════════════════════
# 阶段 1: 准备
# ═══════════════════════════════════════════

def prepare(arm):
    for coll in arm.data.collections:
        coll.is_visible = True
    print("[OK] 所有骨骼集合已显示")

    bpy.ops.object.mode_set(mode='POSE')
    removed = 0
    for pb in arm.pose.bones:
        for c in list(pb.constraints):
            pb.constraints.remove(c)
            removed += 1
    _enter_object()
    print(f"[OK] 已移除 {removed} 个骨骼约束")

    bpy.ops.object.mode_set(mode='POSE')
    for pb in arm.pose.bones:
        pb.lock_location = (False, False, False)
        pb.lock_rotation = (False, False, False)
        pb.lock_rotation_w = False
        pb.lock_scale = (False, False, False)
    _enter_object()
    print("[OK] 所有骨骼变换已解锁")

# ═══════════════════════════════════════════
# 阶段 2: 记录层级
# ═══════════════════════════════════════════

def save_hierarchy(arm, bones_to_keep):
    """
    为每个会保留的骨骼，找到最近的也"会保留"的祖先，
    跳过中间会被删除的骨骼。
    返回: {bone_name: parent_name_or_None}
    """
    hierarchy = {}
    bones = arm.data.bones

    for bone in bones:
        if bone.name not in bones_to_keep:
            continue

        parent = bone.parent
        while parent:
            if parent.name in bones_to_keep:
                hierarchy[bone.name] = parent.name
                break
            parent = parent.parent

        if bone.name not in hierarchy:
            hierarchy[bone.name] = None  # 顶层

    print(f"[OK] 记录了 {len(hierarchy)} 个骨骼的层级关系")
    return hierarchy

# ═══════════════════════════════════════════
# 阶段 3: 删除无权重骨骼
# ═══════════════════════════════════════════

def delete_bones_without_weight(arm, bones_to_keep):
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm.data.edit_bones
    to_delete = [b.name for b in edit_bones if b.name not in bones_to_keep]

    for name in to_delete:
        edit_bones.remove(edit_bones[name])

    remaining = len(edit_bones)
    _enter_object()
    print(f"[OK] 已删除 {len(to_delete)} 个无权重骨骼，保留 {remaining} 个")

# ═══════════════════════════════════════════
# 阶段 4: 重命名
# ═══════════════════════════════════════════

def strip_prefixes(arm):
    bpy.ops.object.mode_set(mode='EDIT')
    count = 0
    for bone in arm.data.edit_bones:
        for prefix in STRIP_PREFIXES:
            if bone.name.startswith(prefix):
                bone.name = bone.name[len(prefix):]
                count += 1
                break
    _enter_object()
    print(f"[OK] 已去除 {count} 个骨骼的前缀")

def replace_chars(arm):
    bpy.ops.object.mode_set(mode='EDIT')
    count = 0
    for bone in arm.data.edit_bones:
        if CHAR_TO_REPLACE in bone.name:
            bone.name = bone.name.replace(CHAR_TO_REPLACE, CHAR_REPLACEMENT)
            count += 1
    _enter_object()
    print(f"[OK] 已将 {count} 个骨骼名的 '{CHAR_TO_REPLACE}' 替换为 '{CHAR_REPLACEMENT}'")

def apply_ue_map(arm):
    bpy.ops.object.mode_set(mode='EDIT')
    count = 0
    for bone in arm.data.edit_bones:
        new = bone.name
        for old, new_str in UE_NAME_MAP.items():
            if old in new:
                new = new.replace(old, new_str)
        if new != bone.name:
            bone.name = new
            count += 1
    _enter_object()
    print(f"[OK] 已对 {count} 个骨骼应用 UE 命名映射")

# ═══════════════════════════════════════════
# 阶段 5: 同步 Vertex Group
# ═══════════════════════════════════════════

def sync_vertex_groups(arm):
    """
    Blender 会自动在骨骼改名时同步同名 vertex group。
    此函数清理那些改名后"不对应任何骨骼"的孤立 VG。
    """
    meshes = _get_bound_meshes(arm)
    if not meshes:
        return

    bone_names = {b.name for b in arm.data.bones}
    deleted = 0

    for mesh_obj in meshes:
        to_remove = []
        for vg in mesh_obj.vertex_groups:
            if vg.name not in bone_names:
                # 尝试标准化匹配（去前缀→替换字符→UE映射）
                name = vg.name
                for p in STRIP_PREFIXES:
                    if name.startswith(p):
                        name = name[len(p):]
                        break
                name = name.replace(CHAR_TO_REPLACE, CHAR_REPLACEMENT)
                for old, new_str in UE_NAME_MAP.items():
                    if old in name:
                        name = name.replace(old, new_str)
                if name in bone_names:
                    vg.name = name
                else:
                    to_remove.append(vg)

        for vg in to_remove:
            mesh_obj.vertex_groups.remove(vg)
            deleted += 1

    if deleted:
        print(f"[OK] 已删除 {deleted} 个孤立 vertex group")

# ═══════════════════════════════════════════
# 阶段 6: 重建层级
# ═══════════════════════════════════════════

def _normalize(name):
    for p in STRIP_PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
            break
    name = name.replace(CHAR_TO_REPLACE, CHAR_REPLACEMENT)
    for old, new_str in UE_NAME_MAP.items():
        if old in name:
            name = name.replace(old, new_str)
    return name.lower()

def create_root_bone(arm):
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm.data.edit_bones

    if "root" not in edit_bones:
        root = edit_bones.new(name="root")
        root.head = (0, 0, 0)
        root.tail = (0, 0.1, 0)
        root.roll = 0

    root = edit_bones["root"]
    orphans = 0
    for bone in edit_bones:
        if bone != root and bone.parent is None:
            bone.parent = root
            bone.use_connect = False
            orphans += 1

    _enter_object()
    print(f"[OK] root 已创建，{orphans} 个孤儿骨骼已挂载")

def rebuild_hierarchy(arm, old_hierarchy):
    """根据旧层级重建父子关系"""
    bone_names = {b.name for b in arm.data.bones}

    # 构建标准化索引
    norm_to_name = {}
    for bn in bone_names:
        norm = _normalize(bn)
        norm_to_name[norm] = bn

    # old → new 映射
    name_map = {}
    for old_name in old_hierarchy:
        norm = _normalize(old_name)
        if norm in norm_to_name:
            name_map[old_name] = norm_to_name[norm]

    print(f"[OK] 构建了 {len(name_map)} 条名称映射")

    # 设置父级
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm.data.edit_bones
    parented = 0

    for old_name, old_parent in old_hierarchy.items():
        new_name = name_map.get(old_name)
        if not new_name or new_name not in edit_bones:
            continue

        bone = edit_bones[new_name]

        if old_parent:
            new_parent = name_map.get(old_parent)
            if new_parent and new_parent in edit_bones and new_parent != new_name:
                bone.parent = edit_bones[new_parent]
                bone.use_connect = False
                parented += 1

    _enter_object()
    print(f"[OK] 已重建 {parented} 条父子关系")


def _infer_parent_side(name):
    """从名称推断 left/right side"""
    n = name.lower()
    if n.endswith("_r") or "_r_" in n:
        return "r"
    return "l"

def set_parents_by_convention(arm):
    """为所有无父级的骨骼按命名规则自动设置父级"""
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm.data.edit_bones
    lower = {b.name.lower(): b for b in edit_bones}
    count = 0

    # 优先级规则
    rules = [
        # (匹配正则, 父级查找逻辑)
        (r"spine_(\d+)", lambda m: lower.get(f"spine_{(int(m.group(1))-1):03d}")
                                   or lower.get(f"spine_{(int(m.group(1))-1):02d}")
                                   or lower.get(f"spine_{(int(m.group(1))-1)}")
                                   or lower.get("pelvis")),
        (r"^neck_?(\d*)$", lambda m: lower.get("spine_003") or lower.get("spine_002")),
        (r"^head$", lambda m: lower.get("neck_01") or lower.get("neck")),
        (r"^clavicle", lambda m: lower.get("spine_003") or lower.get("spine_002")),
        (r"^upperarm", lambda m: lower.get(f"clavicle_{_infer_parent_side(m.string)}")),
        (r"^lowerarm", lambda m: lower.get(f"upperarm_{_infer_parent_side(m.string)}")),
        (r"^hand(?!_)", lambda m: lower.get(f"lowerarm_{_infer_parent_side(m.string)}")),
        (r"^thigh", lambda m: lower.get(f"pelvis_{_infer_parent_side(m.string)}") or lower.get("pelvis")),
        (r"^calf", lambda m: lower.get(f"thigh_{_infer_parent_side(m.string)}")),
        (r"^foot", lambda m: lower.get(f"calf_{_infer_parent_side(m.string)}")),
        (r"^ball", lambda m: lower.get(f"foot_{_infer_parent_side(m.string)}")),
        (r"eyebone", lambda m: lower.get("head")),
    ]

    finger_bases = ["thumb", "f_index", "f_middle", "f_ring", "f_pinky", "palm"]

    for bone in list(edit_bones):
        if bone.parent or bone.name == "root":
            continue

        matched = False
        name_lower = bone.name.lower()

        # 手指
        for fb in finger_bases:
            if fb in name_lower:
                side = _infer_parent_side(name_lower)
                # finger.01 的父级是 hand，finger.02 的父级是 finger.01...
                if ".01" in name_lower or "_01" in name_lower:
                    parent = lower.get(f"hand_{side}")
                else:
                    # finger.0X → finger.0(X-1)
                    for suffix in ["_03", "_02", ".03", ".02"]:
                        if suffix in name_lower:
                            prev = name_lower.replace("03", "02").replace("02", "01")
                            parent = lower.get(prev)
                            break
                    else:
                        parent = lower.get(f"hand_{side}")
                if parent and parent != bone:
                    bone.parent = parent
                    bone.use_connect = False
                    count += 1
                    matched = True
                break

        if matched:
            continue

        # 通用规则
        for pattern, get_parent in rules:
            m = re.search(pattern, name_lower)
            if m:
                parent = get_parent(m)
                if parent and parent != bone:
                    bone.parent = parent
                    bone.use_connect = False
                    count += 1
                break

    _enter_object()
    print(f"[OK] 按命名规则为 {count} 个骨骼设置了父级")


# ═══════════════════════════════════════════
# 阶段 0: 单位 & 缩放
# ═══════════════════════════════════════════

def setup_units_and_scale():
    """
    1. 单位系统 → 公制 / 厘米 / 缩放 0.01
    2. 选中物体放大 100 倍
    3. Ctrl+A 应用全部变换（位置 + 旋转 + 缩放）
    """
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.length_unit = 'CENTIMETERS'
    scene.unit_settings.scale_length = 0.01
    print("[OK] 单位: 公制 / 厘米 / 缩放 0.01")

    selected = [o for o in bpy.context.selected_objects]
    if not selected:
        print("[WARN] 未选中任何物体，跳过缩放 & 应用变换")
        return

    _enter_object()
    # 确保选中物体保持选中状态
    bpy.ops.object.select_all(action='DESELECT')
    for obj in selected:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = selected[0]

    # 放大 100 倍
    bpy.ops.transform.resize(value=(100, 100, 100))
    print(f"[OK] {len(selected)} 个物体已缩放 100x")

    # Ctrl+A → 应用全部变换
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    print("[OK] 已应用全部变换")


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    print("=" * 60)
    print("ProcessingCharacter.py v3 — Bone Only")
    print("=" * 60)

    # ── 0. 单位 & 缩放 ──
    print("\n── 阶段 0: 单位 & 缩放 ──")
    setup_units_and_scale()

    arm = _get_armature()
    print(f"\n骨架: {arm.name}  |  初始骨骼: {len(arm.data.bones)}")
    meshes = _get_bound_meshes(arm)
    print(f"绑定 Mesh: {len(meshes)} 个")

    # ── 1. 准备 ──
    print("\n── 阶段 1: 准备 ──")
    prepare(arm)

    # ── 关键: 先确定哪些骨骼有顶点权重 ──
    bones_with_weight = _get_bones_with_weight(arm, meshes)
    print(f"[INFO] 有权重的骨骼: {len(bones_with_weight)} 个")

    # ── 2. 记录层级 ──
    print("\n── 阶段 2: 记录层级 ──")
    saved = save_hierarchy(arm, bones_with_weight)

    # ── 3. 删除无权重骨骼 ──
    print("\n── 阶段 3: 删除无权重骨骼 ──")
    delete_bones_without_weight(arm, bones_with_weight)
    print(f"   剩余骨骼: {len(arm.data.bones)}")

    # ── 4. 重命名 ──
    print("\n── 阶段 4: 重命名 ──")
    strip_prefixes(arm)
    replace_chars(arm)
    apply_ue_map(arm)

    # ── 5. 同步 Vertex Group ──
    print("\n── 阶段 5: 同步 Vertex Group ──")
    sync_vertex_groups(arm)

    # ── 6. 重建层级 ──
    print("\n── 阶段 6: 重建层级 ──")
    create_root_bone(arm)
    rebuild_hierarchy(arm, saved)
    set_parents_by_convention(arm)  # 补充: 命名规则推断

    # ── 保存 ──
    _enter_object()
    try:
        filepath = bpy.data.filepath.replace('\\', '/')
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        print("\n[OK] 文件已保存")
    except Exception as e:
        print(f"\n[WARN] 保存失败: {e}")

    print(f"\n{'='*60}")
    print(f"完成! 最终骨骼: {len(arm.data.bones)}")
    print(f"{'='*60}\n")


# ═══════════════════════════════════════════
# 可选: UE IK 骨骼
# ═══════════════════════════════════════════

IK_BONE_LENGTH = 0.1


def create_ik_bones(arm):
    """创建 UE 风格脚部 + 手部 IK 骨骼（大小写不敏感查找）"""
    _enter_object()
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm.data.edit_bones
    bone_lower = {b.name.lower(): b for b in edit_bones}
    z_up = mathutils.Vector((0, 0, 1))

    if "root" not in edit_bones:
        print("[WARN] 未找到 root，跳过 IK 创建")
        _enter_object()
        return

    root = edit_bones["root"]

    # ── ik_foot_root ──
    ik_foot_root = edit_bones.get("ik_foot_root")
    if not ik_foot_root:
        ik_foot_root = edit_bones.new(name="ik_foot_root")
        ik_foot_root.head = root.head.copy()
        ik_foot_root.tail = ik_foot_root.head + z_up * IK_BONE_LENGTH
        ik_foot_root.parent = root
        ik_foot_root.use_connect = False
        ik_foot_root.roll = 0
        print("  + ik_foot_root")

    for side, target in [("l", "foot_l"), ("r", "foot_r")]:
        ik_name = f"ik_foot_{side}"
        if ik_name in edit_bones:
            continue
        src = bone_lower.get(target)
        if src:
            ik = edit_bones.new(name=ik_name)
            ik.head = src.head.copy()
            ik.tail = ik.head + z_up * IK_BONE_LENGTH
            ik.parent = ik_foot_root
            ik.use_connect = False
            ik.roll = 0
            print(f"  + {ik_name}")
        else:
            print(f"  [WARN] 未找到 {target}")

    # ── ik_hand_root ──
    ik_hand_root = edit_bones.get("ik_hand_root")
    if not ik_hand_root:
        ik_hand_root = edit_bones.new(name="ik_hand_root")
        ik_hand_root.head = root.head.copy()
        ik_hand_root.tail = ik_hand_root.head + z_up * IK_BONE_LENGTH
        ik_hand_root.parent = root
        ik_hand_root.use_connect = False
        ik_hand_root.roll = 0
        print("  + ik_hand_root")

    src_r = bone_lower.get("hand_r")
    if src_r:
        ik_gun = edit_bones.get("ik_hand_gun")
        if not ik_gun:
            ik_gun = edit_bones.new(name="ik_hand_gun")
            ik_gun.head = src_r.head.copy()
            ik_gun.tail = ik_gun.head + z_up * IK_BONE_LENGTH
            ik_gun.parent = ik_hand_root
            ik_gun.use_connect = False
            ik_gun.roll = 0
            print("  + ik_hand_gun")

        for name, target in [("ik_hand_r", "hand_r"), ("ik_hand_l", "hand_l")]:
            if name in edit_bones:
                continue
            src = bone_lower.get(target)
            if src:
                ik = edit_bones.new(name=name)
                ik.head = src.head.copy()
                ik.tail = ik.head + z_up * IK_BONE_LENGTH
                ik.parent = ik_gun
                ik.use_connect = False
                ik.roll = 0
                print(f"  + {name}")
    else:
        print("  [WARN] 未找到 hand_r，跳过手部 IK")

    _enter_object()
    print("[OK] IK 骨骼创建完成\n")


def create_sdf_bones(arm, forward_dist=0.5, right_dist=0.5):
    """
    创建面部阴影 SDF 参考骨骼（基于 head）:
      SDF_F → head 子级，向前 (-Y)  → 正面阴影阈值
      SDF_R → head 子级，向右 (-X)  → 侧面阴影阈值
    """
    _enter_object()
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm.data.edit_bones
    bone_lower = {b.name.lower(): b for b in edit_bones}

    src_head = bone_lower.get("head")
    if not src_head:
        print("[WARN] 未找到 head，跳过 SDF 创建")
        _enter_object()
        return

    if "SDF_F" not in edit_bones:
        sdf_f = edit_bones.new(name="SDF_F")
        sdf_f.head = src_head.head.copy()
        sdf_f.tail = src_head.tail.copy()
        sdf_f.roll = src_head.roll
        sdf_f.parent = src_head
        sdf_f.use_connect = False
        sdf_f.head.y -= forward_dist
        sdf_f.tail.y -= forward_dist
        print("  + SDF_F (向前)")
    else:
        print("  SDF_F 已存在")

    if "SDF_R" not in edit_bones:
        sdf_r = edit_bones.new(name="SDF_R")
        sdf_r.head = src_head.head.copy()
        sdf_r.tail = src_head.tail.copy()
        sdf_r.roll = src_head.roll
        sdf_r.parent = src_head
        sdf_r.use_connect = False
        sdf_r.head.x -= right_dist
        sdf_r.tail.x -= right_dist
        print("  + SDF_R (向右)")
    else:
        print("  SDF_R 已存在")

    _enter_object()
    print("[OK] SDF 骨骼创建完成\n")


if __name__ == "__main__":
    main()
    # 可选: 取消注释以自动创建 IK / SDF 骨骼
    create_ik_bones(_get_armature())
    create_sdf_bones(_get_armature())
