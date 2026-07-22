"""
ProcessingCharacterMesh.py — 模型清理（不碰骨骼）
==============================================
1. 移除未使用的材质插槽
2. 移除所有非动画修改器（仅保留 Armature / Shape Key / Lattice）

用法: 选中骨架 → 运行脚本（自动找所有绑定 Mesh）
     或直接选中 Mesh → 运行脚本
"""

import bpy


# 保留的修改器类型（动画相关）
KEEP_MODIFIERS = {'ARMATURE', 'SHAPE_KEY', 'LATTICE'}


def get_targets():
    """
    获取要处理的 Mesh 列表。
    优先从选中的骨架查找绑定 Mesh，其次使用直接选中的 Mesh。
    """
    # 优先: 从选中骨架找
    for obj in bpy.context.selected_objects:
        if obj.type == 'ARMATURE':
            meshes = []
            for o in bpy.data.objects:
                if o.type != 'MESH':
                    continue
                for mod in o.modifiers:
                    if mod.type == 'ARMATURE' and mod.object == obj:
                        meshes.append(o)
                        break
            return meshes

    # 兜底: 直接用选中的 Mesh
    return [o for o in bpy.context.selected_objects if o.type == 'MESH']


# ═══════════════════════════════════════════
# 1. 移除未使用的材质插槽
# ═══════════════════════════════════════════

def remove_unused_material_slots(meshes):
    removed_total = 0

    for obj in meshes:
        if not obj.material_slots:
            continue

        bpy.context.view_layer.objects.active = obj

        # 收集所有 face 正在使用的材质索引
        used = set()
        for poly in obj.data.polygons:
            used.add(poly.material_index)

        slots = len(obj.material_slots)
        unused = [i for i in range(slots) if i not in used]

        # 反向删除（避免索引错乱）
        for idx in sorted(unused, reverse=True):
            obj.active_material_index = idx
            bpy.ops.object.material_slot_remove()
            removed_total += 1

        if unused:
            print(f"  {obj.name}: -{len(unused)} 个空材质槽")

    print(f"[OK] 移除 {removed_total} 个未使用的材质槽")


# ═══════════════════════════════════════════
# 2. 移除非动画修改器
# ═══════════════════════════════════════════

def remove_non_animation_modifiers(meshes):
    removed_total = 0

    for obj in meshes:
        # 先收集名字，避免迭代中集合变化
        to_remove = []
        for mod in obj.modifiers:
            if mod.type not in KEEP_MODIFIERS:
                to_remove.append(mod.name)

        for name in to_remove:
            try:
                obj.modifiers.remove(obj.modifiers[name])
                removed_total += 1
            except Exception:
                pass

        if to_remove:
            print(f"  {obj.name}: -{to_remove}")

    print(f"[OK] 移除 {removed_total} 个非动画修改器")


# ═══════════════════════════════════════════
# 3. 按材质分离 + 命名
# ═══════════════════════════════════════════

def separate_by_material(mesh_obj):
    """
    将选中的 Mesh 按材质分离为独立对象，
    每个新对象命名为 "原名称_材质名"。
    """
    if mesh_obj.type != 'MESH':
        return []

    # 记录分离前的场景对象
    before = set(bpy.data.objects)

    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.separate(type='MATERIAL')
    bpy.ops.object.mode_set(mode='OBJECT')

    # 找出新创建的对象
    after = set(bpy.data.objects)
    new_objects = list(after - before)

    # 也包括原始对象（它可能变成了其中一个材质的部分）
    all_parts = [mesh_obj] + new_objects

    renamed = []
    for obj in all_parts:
        if not obj.material_slots:
            continue
        mat = obj.material_slots[0].material
        if mat:
            new_name = f"{mat.name}"
            # 避免重名
            if new_name not in bpy.data.objects:
                obj.name = new_name
            else:
                obj.name = f"{mat.name}_split"
            renamed.append(obj.name)

    return renamed


def separate_all(meshes):
    """
    对列表中所有 Mesh 按材质分离。
    返回所有 Mesh 对象列表（包含分离出的新部件），
    供后续步骤（如描边）对完整集合操作。
    """
    all_meshes = list(meshes)
    total = 0
    for obj in meshes:
        before_count = len(all_meshes)
        parts = separate_by_material(obj)
        if parts:
            print(f"  {obj.name} → {parts}")
            total += len(parts)
            # 将新创建的分离对象加入列表
            for name in parts:
                new_obj = bpy.data.objects.get(name)
                if new_obj and new_obj not in all_meshes:
                    all_meshes.append(new_obj)
            # 保留原对象（如果还在列表中且未被合并）
            if obj not in all_meshes and obj.name in bpy.data.objects:
                all_meshes.append(obj)

    print(f"[OK] 分离出 {total} 个材质部件，当前共 {len(all_meshes)} 个 Mesh")
    return all_meshes


# ═══════════════════════════════════════════
# 4. 移除空的形态键（与 Basis 无差异的）
# ═══════════════════════════════════════════

def remove_empty_shape_keys(meshes):
    """
    移除那些与 Basis 完全没有差异的形态键。
    Genshin 模型经常带有大量空置的 shape key。
    """
    removed_total = 0

    for obj in meshes:
        if not obj.data.shape_keys:
            continue
        kb = obj.data.shape_keys.key_blocks
        if len(kb) <= 1:
            continue  # 只有 Basis，不管

        basis = kb[0]
        to_remove = []

        for key in kb[1:]:  # 跳过 Basis
            # 比较每个顶点
            is_identical = True
            for i, vert in enumerate(obj.data.vertices):
                if key.data[i].co != basis.data[i].co:
                    is_identical = False
                    break
            if is_identical:
                to_remove.append(key.name)

        for name in to_remove:
            obj.shape_key_remove(kb[name])
            removed_total += 1

        if to_remove:
            print(f"  {obj.name}: -{to_remove}")

    print(f"[OK] 移除 {removed_total} 个空形态键")


# ═══════════════════════════════════════════
# 5. 外描边（原地复制 → 实体化 → 应用 → 删原面 → 合并）
# ═══════════════════════════════════════════

def _apply_modifier_safe(obj, mod_name):
    """
    安全应用修改器（兼容形态键）。
    """
    bpy.context.view_layer.objects.active = obj

    has_shape_keys = obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) > 0

    if has_shape_keys:
            collection_params = [{'name':mod_name , 'apply_modifier': True}]
            bpy.ops.object.apply_modifiers_with_shape_keys(collection_property=collection_params)

    else:
        bpy.ops.object.modifier_apply(modifier=mod_name)



OUTLINE_THICKNESS = 0.0015
OUTLINE_SUFFIX = " Outline"
MERGE_DISTANCE = 0.0001  # 描边前合并顶点阈值（避免断裂）


def _get_or_create_outline_mat(mesh_name):
    """按 Mesh 名创建/获取专属描边材质，例如 Body → BodyOutline"""
    mat_name = f"{mesh_name}{OUTLINE_SUFFIX}"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
        mat.use_backface_culling = True
    return mat


def add_outline_to_mesh(mesh_obj, thickness=OUTLINE_THICKNESS):
    """
    给单个 Mesh 添加外描边（合并到自身）：
      1. 原地复制
      2. 副本统一材质 → 加 Outline 材质 → 实体化修改器
      3. 应用修改器
      4. 删非描边面（只留实体化生成的面）
      5. 清理 → 合并回原 Mesh
    """
    if mesh_obj.type != 'MESH':
        return

    orig_name = mesh_obj.name
    outline_mat = _get_or_create_outline_mat(orig_name)

    # ── 1. 原地复制 ──
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.duplicate(linked=False)
    dup = bpy.context.active_object

    # ── 1.5 按距离合并顶点（消除裂缝，避免描边断裂） ──
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=MERGE_DISTANCE)
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── 2. 统一材质 → 所有面用 slot 0，Outline 放 slot 1 ──
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')

    # 确保至少有 slot 0
    if not dup.data.materials:
        placeholder = bpy.data.materials.new(name="__tmp__")
        dup.data.materials.append(placeholder)
    dup.active_material_index = 0
    bpy.ops.object.material_slot_assign()

    # 添加 Outline 到 slot 1
    dup.data.materials.append(outline_mat)
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── 3. 实体化修改器（所有源面用 slot 0，新面自动用 slot 1） ──
    bpy.context.view_layer.objects.active = dup
    dup.select_set(True)
    mod = dup.modifiers.new(name="Outline_Solidify", type='SOLIDIFY')
    print(f"    + Solidify: {dup.name}")
    mod.thickness = thickness
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_quality_normals = True
    mod.material_offset = 1     # 源面 slot 0 + 1 → 新面 slot 1
    for attr in ('use_rim', 'use_fill_rim'):
        try:
            setattr(mod, attr, False)
            break
        except (AttributeError, TypeError):
            continue

    # ── 4. 应用（兼容形态键） ──
    bpy.context.view_layer.objects.active = dup
    _apply_modifier_safe(dup, mod.name)

    # ── 5. 删除非描边面 → 只留 slot 1 的面 ──
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    dup.active_material_index = 1
    try:
        bpy.ops.object.material_slot_select()
        bpy.ops.mesh.select_all(action='INVERT')
        bpy.ops.mesh.delete(type='FACE')
    except RuntimeError:
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(dup, do_unlink=True)
        print(f"  {orig_name}: 无可生成描边的面，已跳过")
        return
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── 6. 清理素材槽（只保留 Outline） ──
    for i in range(len(dup.material_slots) - 1, -1, -1):
        if dup.material_slots[i].material != outline_mat:
            dup.active_material_index = i
            bpy.ops.object.material_slot_remove()

    # ── 7. 合并回原 Mesh ──
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    dup.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.join()

    # 去重材质槽
    _dedupe_material_slots(mesh_obj)

    print(f"  {orig_name}: 描边已合并")


def _dedupe_material_slots(obj):
    """合并重复的材质槽"""
    seen = {}
    to_remove = []
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat and mat.name in seen:
            to_remove.append(i)
        elif mat:
            seen[mat.name] = i
    for idx in sorted(to_remove, reverse=True):
        obj.active_material_index = idx
        bpy.ops.object.material_slot_remove()


def add_outline_to_all(meshes, thickness=OUTLINE_THICKNESS):
    """给列表中所有 Mesh 添加描边"""
    for obj in meshes:
        if obj.type != 'MESH':
            continue
        add_outline_to_mesh(obj, thickness)
    print(f"[OK] 描边处理完成")


def add_outline_to_selected():
    """给当前选中的 Mesh 添加描边"""
    meshes = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    if not meshes:
        print("请选中需要描边的 Mesh")
        return
    add_outline_to_all(meshes)


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    meshes = get_targets()
    if not meshes:
        print("未找到要处理的 Mesh！请选中骨架或直接选中 Mesh。")
        return

    print(f"处理 {len(meshes)} 个 Mesh:\n  " + "\n  ".join(m.name for m in meshes))
    print()

    remove_unused_material_slots(meshes)
    remove_non_animation_modifiers(meshes)
    remove_empty_shape_keys(meshes)
    # 分离后更新 meshes 列表，确保新建的分离部件也参与后续处理
    meshes = separate_all(meshes)
    add_outline_to_all(meshes)

    print(f"\n完成。")


if __name__ == "__main__":
    main()
