import array
import bpy
from collections import OrderedDict
import hashlib
import json
import mathutils
import math
import os
import shutil
import struct
import time
import cProfile

import blend4avango

from .b4a_bin_suffix import get_platform_suffix
libname = "b4a_bin" + get_platform_suffix()
try:
    exec("from . import " + libname + " as b4a_bin")
except:
    # NOTE: check later in register() function
    pass

from . import anim_baker
from . import nla_script

BINARY_INT_SIZE = 4
BINARY_SHORT_SIZE = 2
BINARY_FLOAT_SIZE = 4

MSG_SYMBOL_WIDTH = 6

JSON_PRETTY_PRINT = False
SUPPORTED_OBJ_TYPES = ["MESH", "CURVE", "ARMATURE", "EMPTY", "CAMERA", "LAMP", \
        "SPEAKER"]
SUPPORTED_NODES = ["NodeFrame", "ShaderNodeMaterial", "ShaderNodeCameraData", \
        "ShaderNodeValue", "ShaderNodeRGB", "ShaderNodeTexture", \
        "ShaderNodeGeometry", "ShaderNodeExtendedMaterial", "ShaderNodeLampData", \
        "ShaderNodeOutput", "ShaderNodeMixRGB", "ShaderNodeRGBCurve", \
        "ShaderNodeInvert", "ShaderNodeHueSaturation", "ShaderNodeNormal", \
        "ShaderNodeMapping", "ShaderNodeVectorCurve", "ShaderNodeValToRGB", \
        "ShaderNodeRGBToBW", "ShaderNodeMath", "ShaderNodeVectorMath", \
        "ShaderNodeSqueeze", "ShaderNodeSeparateRGB", "ShaderNodeCombineRGB", \
        "ShaderNodeSeparateHSV", "ShaderNodeCombineHSV", \
        "NodeReroute", "ShaderNodeGroup", "NodeGroupInput", "NodeGroupOutput"]

# globals

# weak reference is not supported
_bpy_bindata_int = bytearray();
_bpy_bindata_float = bytearray();
_bpy_bindata_short = bytearray();
_bpy_bindata_ushort = bytearray();

_export_data = None
_main_json_str = ""

_export_uuid_cache = None
_bpy_uuid_cache = None

_overrided_meshes = []

_is_html_export = False

_export_filepath = None
_export_error = None
_file_error = None

_scene_active_layers = {}

_b4a_export_warnings = []

_b4a_export_errors = []

_vehicle_integrity = {}

_packed_files_data = {}

# currently processed data
_curr_scene = None
_curr_mesh = None
_curr_material_stack = []

_fallback_camera = None
_fallback_world = None
_fallback_material = None

# temp property will exist to the end of session
# and will not be saved to blend file
PATH_RESOLVED = "path_resolved"

# speaker distance maximum
SPKDISTMAX = 10000

NCOMP = 3

COL_NUM_COMP = 3

class MaterialError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message

class ExportError(Exception):
    def __init__(self, message, component, comment=None):
        self.message = message
        self.component_name = component.name
        self.component_type = component.rna_type.name
        self.comment = comment
        clean_exported_data()

    def __str__(self):
        return "Export error: " + self.component_name + ": " + self.message

class InternalError(Exception):
    def __init__(self, message=None):
        self.message = message
        clean_exported_data()

    def __str__(self):
        if (self.message):
            return "b4a internal error: " + self.message
        else:
            return "b4a internal error: unknown"

class FileError(Exception):
    def __init__(self, message=None):
        self.message = message
        clean_exported_data()
    def __str__(self):
        return "Export file error: " + self.message

class ExportErrorDialog(bpy.types.Operator):
    bl_idname = "b4a.export_error_dialog"
    bl_label = "Export Error Dialog"

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        window_width = calc_export_error_window_width()
        return wm.invoke_props_dialog(self, window_width)

    def draw(self, context):
        global _export_error

        print(_export_error)

        row = self.layout.row()
        row.alignment = "CENTER"
        row.label("=== BLEND4WEB: EXPORT ERROR ===")
        row = self.layout.row()
        row.label("COMPONENT: " + _export_error.component_type.upper())
        row = self.layout.row()
        row.label("NAME: " + _export_error.component_name)
        row = self.layout.row()
        row.label("ERROR: " + _export_error.message)
        if _export_error.comment:
            row = self.layout.row()
            row.label(_export_error.comment)

class FileErrorDialog(bpy.types.Operator):
    bl_idname = "b4a.file_error_dialog"
    bl_label = "File Error Dialog"

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        window_width = calc_file_error_window_width()
        return wm.invoke_props_dialog(self, window_width)

    def draw(self, context):
        global _file_error

        print(_file_error)

        row = self.layout.row()
        row.alignment = "CENTER"
        row.label("=== BLEND4AVANGO: FILE ERROR ===")
        row = self.layout.row()
        row.label("ERROR: " + _file_error.message)
        row = self.layout.row()

def calc_export_error_window_width():

    global _export_error

    num_symbols = 0

    if _export_error.message:
        num_symbols = len("ERROR: "+_export_error.message)

    if _export_error.comment:
        num_symbols = max(len(_export_error.comment), num_symbols)

    window_width = num_symbols * MSG_SYMBOL_WIDTH
    window_width = max(window_width, 220)

    return window_width

def calc_file_error_window_width():

    global _file_error

    num_symbols = 0

    if _file_error.message:
        num_symbols = len("ERROR: "+_file_error.message)

    window_width = num_symbols * MSG_SYMBOL_WIDTH
    window_width = max(window_width, 220)

    return window_width

def warn(message):
    _b4a_export_warnings.append(message)

def err(message):
    _b4a_export_errors.append(message)

def get_filepath_blend(export_filepath):
    """return path to blend relative to json"""
    blend_abs = bpy.data.filepath

    if blend_abs:
        json_abs = export_filepath

        try:
            blend_rel = os.path.relpath(blend_abs, os.path.dirname(json_abs))
        except ValueError as exp:
            _file_error = exp
            raise FileError("Export to different disk is forbidden")

        return guard_slashes(os.path.normpath(blend_rel))
    else:
        return ""

# some data components are not needed for the engine
# so assign "b4a_do_not_export" flags to them
# in order to reduce file size and processing power
def assign_do_not_export_flags():

    # we don't need bone custom shapes
    for obj in bpy.data.objects:
        pose = obj.pose
        if pose:
            for pbone in pose.bones:
                shape = pbone.custom_shape
                if shape:
                    shape.b4a_do_not_export = True

    # render result
    for img in bpy.data.images:
        if img.source == "VIEWER":
            img.b4a_do_not_export = True

def attach_export_properties(tags):
    for tag in tags:
        source = getattr(bpy.data, tag)
        for component in source:
            component["export_done"] = False
    for mat in bpy.data.materials:
        mat["in_use"] = False

def detach_export_properties(tags):
    for tag in tags:
        source = getattr(bpy.data, tag)
        for component in source:
            if "export_done" in component:
                del component["export_done"]

def gen_uuid(comp):
    # type + name + lib path
    s = comp.rna_type.name + comp.name
    if comp.library:
        s += comp.library.filepath

    uuid = hashlib.md5(s.encode()).hexdigest()
    return uuid

def gen_uuid_obj(comp):
    if comp:
        return OrderedDict({ "uuid": gen_uuid(comp) })
    else:
        return ""

def guard_slashes(path):
    return path.replace('\\', '/')

def do_export(component):
    return not component.b4a_do_not_export

def object_is_valid(obj):
    return obj.type in SUPPORTED_OBJ_TYPES

def particle_object_is_valid(obj):
    return obj.type == "MESH"

def get_component_export_path(component):
    # deprecated but may be stored in older files
    if component.get("b4a_export_path", None) is not None:
        b4a_export_path = component.b4a_export_path
        if len(b4a_export_path) > 0:
            return b4a_export_path

    return ""

def obj_to_mesh_needed(obj):
    """Check if object require copy of obj.data during export"""
    if (obj.type == "MESH" and (obj.b4a_apply_modifiers or
            obj.b4a_loc_export_vertex_anim or obj.b4a_export_edited_normals or
            obj.b4a_apply_scale)):
        return True
    else:
        return False

def get_obj_data(obj, scene):
    data = None

    if obj.data:
        if obj_to_mesh_needed(obj):
            data = obj.to_mesh(scene, obj.b4a_apply_modifiers or obj.b4a_apply_scale, "PREVIEW")
            if obj.b4a_apply_modifiers:
                data.name = obj.name + "_MODIFIERS_APPLIED"
            elif obj.b4a_loc_export_vertex_anim:
                data.name = obj.name + "_VERTEX_ANIM"
            elif obj.b4a_export_edited_normals:
                data.name = obj.name + "_VERTEX_NORMALS"
            elif obj.b4a_apply_scale:
                data.name = obj.name + "_NONUNIFORM_SCALE_APPLIED"
            data.b4a_override_boundings = obj.data.b4a_override_boundings

            data.b4a_boundings.min_x = obj.data.b4a_boundings.min_x
            data.b4a_boundings.min_y = obj.data.b4a_boundings.min_y
            data.b4a_boundings.min_z = obj.data.b4a_boundings.min_z
            data.b4a_boundings.max_x = obj.data.b4a_boundings.max_x
            data.b4a_boundings.max_y = obj.data.b4a_boundings.max_y
            data.b4a_boundings.max_z = obj.data.b4a_boundings.max_z

            if len(data.vertex_colors):
                # NOTE: workaround for blender (v.2.70+) bug - restore vertex
                # colors names
                for i in range(len(obj.data.vertex_colors)):
                    data.vertex_colors[i].name = obj.data.vertex_colors[i].name

            _overrided_meshes.append(data)
        else:
            data = obj.data

    return data

def remove_overrided_meshes():
    for mesh in _overrided_meshes:
        bpy.data.meshes.remove(mesh)

def mesh_get_active_vc(mesh):
    # NOTE: cannot rely on vertex_colors.active or vertex_colors.active_index
    # properties (may be incorrect)
    if mesh.vertex_colors:
        for vc in mesh.vertex_colors:
            if vc.active:
                return vc

        return mesh.vertex_colors[0]

    return None

def scenes_store_select_all_layers():
    global _scene_active_layers
    _scene_active_layers = {}

    for scene in bpy.data.scenes:

        if scene.name not in _scene_active_layers:
            _scene_active_layers[scene.name] = {}

        scene_lib_path = get_scene_lib_path(scene)
        layers = _scene_active_layers[scene.name][scene_lib_path] = []

        for i in range(len(scene.layers)):
            layers.append(scene.layers[i])
            scene.layers[i] = True

def scenes_restore_selected_layers():
    global _scene_active_layers

    for scene in bpy.data.scenes:
        if scene.name in _scene_active_layers:
            scene_lib_path = get_scene_lib_path(scene)
            if scene_lib_path in _scene_active_layers[scene.name]:
                layers_data = _scene_active_layers[scene.name][scene_lib_path]

                for i in range(len(layers_data)):
                    scene.layers[i] = layers_data[i]

def get_scene_lib_path(scene):
    if scene.library:
        return scene.library.filepath
    else:
        return ""

def packed_resource_get_unique_name(resource):
    unique_rsrc_id = resource.name + resource.filepath
    if resource.library:
        unique_rsrc_id += resource.library.filepath
    # fix overwriting collision for export/html export
    if _is_html_export:
        unique_rsrc_id += "%html_export%"

    ext = os.path.splitext(resource.filepath)[1]
    unique_name = hashlib.md5(unique_rsrc_id.encode()).hexdigest() + ext

    if bpy.data.filepath and _export_filepath is not None:
        export_dir = os.path.split(_export_filepath)[0]
        result_name = os.path.join(export_dir, unique_name)
    else:
        result_name = os.path.join(os.getcwd(), unique_name)

    return result_name

def get_main_json_data():
    return _main_json_str

def get_binaries_data():
    return _bpy_bindata_int + _bpy_bindata_float + _bpy_bindata_short \
            + _bpy_bindata_ushort

def get_packed_data():
    return _packed_files_data

def process_components(tags):
    for tag in tags:
        _export_data[tag] = []

    for scene in getattr(bpy.data, "scenes"):
        if do_export(scene):
            process_scene(scene)

    check_shared_data(_export_data)

    for action in getattr(bpy.data, "actions"):
        if do_export(action):
            process_action(action)

def process_action(action):
    if "export_done" in action and action["export_done"]:
        return
    action["export_done"] = True

    act_data = OrderedDict()

    act_data["name"] = action.name
    act_data["uuid"] = gen_uuid(action)
    act_data["frame_range"] = round_iterable(action.frame_range, 2)

    has_decimal_frames = False

    act_data["fcurves"] = OrderedDict()

    # collect fcurves indices
    fc_indices = OrderedDict()
    has_quat_rotation = False
    has_euler_rotation = False
    for i in range(len(action.fcurves)):
        path = action.fcurves[i].data_path

        if path not in fc_indices:
            fc_indices[path] = []
            has_quat_rotation |= path.find("rotation_quaternion") > -1
            has_euler_rotation |= path.find("rotation_euler") > -1

        fc_indices[path].append(i)

    # prefer quaternion rotation
    if has_quat_rotation and has_euler_rotation:
        for data_path in fc_indices:
            if data_path.find("rotation_euler") > -1:
                del fc_indices[data_path]

    for data_path in fc_indices:
        is_scale = data_path.find("scale") > -1
        is_location = data_path.find("location") > -1
        is_rotation_quat = data_path.find("rotation_quaternion") > -1
        is_rotation_euler = data_path.find("rotation_euler") > -1

        for index in fc_indices[data_path]:
            if data_path not in act_data["fcurves"]:
                act_data["fcurves"][data_path] = OrderedDict()
            elif is_scale:
                # expect uniform scales so process only first available channel
                continue

            fcurve = action.fcurves[index]

            # rotate by 90 degrees around x-axis to match standard OpenGL for
            # location and rotation, see pose section for detailed math
            array_index = fcurve_array_index = fcurve.array_index
            if is_scale:
                array_index = 0
            elif is_location or is_rotation_euler: # x y z
                if fcurve_array_index == 1: array_index = 2
                elif fcurve_array_index == 2: array_index = 1
            elif is_rotation_quat: # w x y z
                if fcurve_array_index == 2: array_index = 3
                elif fcurve_array_index == 3: array_index = 2

            keyframes_data = []
            previous = None # init variable
            last_frame_offset = 0 # init variable

            for i in range(len(fcurve.keyframe_points)):
                keyframe_point = fcurve.keyframe_points[i]

                interpolation = keyframe_point.interpolation
                if interpolation == "BEZIER":
                    intercode = 0
                elif interpolation == "LINEAR":
                    intercode = 1
                elif interpolation == "CONSTANT":
                    intercode = 2
                else:
                    raise ExportError("Wrong F-Curve interpolation mode", action,
                            "Only BEZIER, LINEAR or CONSTANT mode is allowed for F-Curve interpolation.")

                co = list(keyframe_point.co)
                hl = list(keyframe_point.handle_left)
                hr = list(keyframe_point.handle_right)

                # NOTE: decimal frames aren't supported, convert to integer
                if co[0] % 1 != 0:
                    co[0] = round(co[0])
                    has_decimal_frames = True

                # rotate by 90 degrees around x-axis to match standard OpenGL
                if (is_location or is_rotation_euler) and fcurve_array_index == 1 \
                        or is_rotation_quat and fcurve_array_index == 2:
                    co = [co[0], -co[1]]
                    hl = [hl[0], -hl[1]]
                    hr = [hr[0], -hr[1]]

                # write to plain array:
                    # interpolation code
                    # control point x and y
                    # left handle   x and y
                    # right handle  x and y

                if (i == len(fcurve.keyframe_points) - 1):
                    last_frame_offset = len(keyframes_data)
                keyframes_data.append(intercode)
                keyframes_data.extend(co)

                # file size optimization: left handle needed only if
                # PREVIOS keyframe point is bezier, right handle needed only if
                # THIS keyframe point is bezier
                if previous and previous.interpolation == "BEZIER":
                    keyframes_data.extend(hl)
                if interpolation == "BEZIER":
                    keyframes_data.extend(hr)

                # save THIS keyframe point as PREVIOS one for the next iteration
                previous = keyframe_point

            keyframes_data_bin = struct.pack("f" * len(keyframes_data),
                    *keyframes_data)

            act_data["fcurves"][data_path][array_index] = OrderedDict();
            act_data["fcurves"][data_path][array_index]["bin_data_pos"] = [
                len(_bpy_bindata_float) // BINARY_FLOAT_SIZE,
                len(keyframes_data_bin) // BINARY_FLOAT_SIZE
            ]
            act_data["fcurves"][data_path][array_index]["last_frame_offset"] \
                    = last_frame_offset

            _bpy_bindata_float.extend(keyframes_data_bin)

    if has_decimal_frames:
        err("The \"" + action.name + "\" action has decimal frames. " +
                "Converted to integer.")

    _export_data["actions"].append(act_data)
    _export_uuid_cache[act_data["uuid"]] = act_data
    _bpy_uuid_cache[act_data["uuid"]] = action

def process_scene(scene):
    if "export_done" in scene and scene["export_done"]:
        return
    scene["export_done"] = True

    global _curr_scene
    _curr_scene = scene

    scene_data = OrderedDict()

    scene_data["name"] = scene.name
    scene_data["uuid"] = gen_uuid(scene)

    process_scene_nla(scene, scene_data)

    scene_data["b4a_enable_audio"] = scene.b4a_enable_audio
    scene_data["b4a_enable_dynamic_compressor"] \
            = scene.b4a_enable_dynamic_compressor

    process_scene_dyn_compr_settings(scene_data, scene)

    scene_data["b4a_enable_convolution_engine"] \
            = scene.b4a_enable_convolution_engine

    scene_data["b4a_enable_physics"] = scene.b4a_enable_physics
    scene_data["b4a_render_shadows"] = scene.b4a_render_shadows
    scene_data["b4a_render_reflections"] = scene.b4a_render_reflections
    scene_data["b4a_render_refractions"] = scene.b4a_render_refractions
    scene_data["b4a_enable_god_rays"] = scene.b4a_enable_god_rays
    scene_data["b4a_enable_ssao"] = scene.b4a_enable_ssao
    scene_data["b4a_batch_grid_size"] = round_num(scene.b4a_batch_grid_size, 2)
    scene_data["b4a_anisotropic_filtering"] = scene.b4a_anisotropic_filtering
    scene_data["b4a_enable_bloom"] = scene.b4a_enable_bloom
    scene_data["b4a_enable_motion_blur"] = scene.b4a_enable_motion_blur
    scene_data["b4a_enable_color_correction"] \
            = scene.b4a_enable_color_correction
    scene_data["b4a_enable_antialiasing"] = scene.b4a_enable_antialiasing

    # process scene links
    scene_data["objects"] = []
    for obj in scene.objects:
        if do_export(obj) and object_is_valid(obj):
            scene_data["objects"].append(gen_uuid_obj(obj))
            process_object(obj)

    camera = scene.camera
    if camera and do_export(camera) and object_is_valid(camera)\
        and camera.type == "CAMERA":
        scene_data["camera"] = gen_uuid_obj(camera)
        process_object(camera)
    else:
        scene_data["camera"] = None

    world = scene.world
    if world and do_export(world):
        scene_data["world"] = gen_uuid_obj(world)
        process_world(world)
    else:
        scene_data["world"] = None

    scene_data["frame_start"] = scene.frame_start
    scene_data["frame_end"] = scene.frame_end

    scene_data["audio_volume"] = round_num(scene.audio_volume, 3)
    scene_data["audio_doppler_speed"] = round_num(scene.audio_doppler_speed, 3)
    scene_data["audio_doppler_factor"] \
            = round_num(scene.audio_doppler_factor, 3)

    _export_data["scenes"].append(scene_data)
    _export_uuid_cache[scene_data["uuid"]] = scene_data
    _bpy_uuid_cache[scene_data["uuid"]] = scene
    check_scene_data(scene_data, scene)

def process_scene_nla(scene, scene_data):
    scene_data["b4a_use_nla"] = scene.b4a_use_nla
    scene_data["b4a_nla_cyclic"] = scene.b4a_nla_cyclic

    scene_data["b4a_nla_script"] = []

    

def process_scene_dyn_compr_settings(scene_data, scene):
    dcompr = scene.b4a_dynamic_compressor_settings

    dct = scene_data["b4a_dynamic_compressor_settings"] = OrderedDict()
    dct["threshold"] = round_num(dcompr.threshold, 1)
    dct["knee"] = round_num(dcompr.knee, 1)
    dct["ratio"] = round_num(dcompr.ratio, 1)
    dct["attack"] = round_num(dcompr.attack, 3)
    dct["release"] = round_num(dcompr.release, 3)

# 2
def process_object(obj):
    if "export_done" in obj and obj["export_done"]:
        return
    obj["export_done"] = True

    obj_data = OrderedDict()

    obj_data["name"] = obj.name
    obj_data["uuid"] = gen_uuid(obj)
    obj_data["type"] = obj.type

    # process object links
    data = get_obj_data(obj, _curr_scene)
    if data is None and obj_data["type"] != "EMPTY":
        raise ExportError("Object data not available", obj,
                "Check the \"Do not export\" flag on the " + obj_data["name"] +
                " data")

    obj_data["data"] = gen_uuid_obj(data)

    # process varyous obj data
    if obj_data["type"] == "MESH":
        process_mesh(data, obj)
    elif obj_data["type"] == "CURVE":
        process_curve(data)
    elif obj_data["type"] == "ARMATURE":
        process_armature(data)
    elif obj_data["type"] == "CAMERA":
        process_camera(data)
    elif obj_data["type"] == "LAMP":
        process_lamp(data)
    elif obj_data["type"] == "SPEAKER":
        process_speaker(data)

    proxy = obj.proxy
    if proxy and do_export(proxy) and object_is_valid(proxy):
        obj_data["proxy"] = gen_uuid_obj(proxy)
        process_object(proxy)
    else:
        obj_data["proxy"] = None

    dupli_group = obj.dupli_group
    if dupli_group:
        obj_data["dupli_group"] = gen_uuid_obj(dupli_group)
        process_group(dupli_group)

        dg_uuid = obj_data["dupli_group"]["uuid"]
        dg_data = _export_uuid_cache[dg_uuid]
        if not dg_data["objects"]:
            raise ExportError("Dupli group error", obj, "Objects from the "  +
                    dg_data["name"] + " dupli group on the object " +
                    obj_data["name"] + " cannot be exported")
    else:
        obj_data["dupli_group"] = None

    parent = obj.parent
    if parent and do_export(parent) and object_is_valid(parent):
        if not is_identity_matrix(obj.matrix_parent_inverse):
            raise ExportError("Object-parent relation is not supported",
                    obj, "Clear the parent's inverse transform")
        obj_data["parent"] = gen_uuid_obj(parent)
        process_object(parent)
    else:
        obj_data["parent"] = None

    obj_data["parent_type"] = obj.parent_type
    obj_data["parent_bone"] = obj.parent_bone

    arm_mod = find_modifier(obj, "ARMATURE")
    # NOTE: give more freedom to objs with edited normals
    obj_data["modifiers"] = []
    if not obj.b4a_apply_modifiers and obj.b4a_export_edited_normals and arm_mod:
        process_object_modifiers(obj_data["modifiers"], [arm_mod])
    elif not obj_to_mesh_needed(obj):
        process_object_modifiers(obj_data["modifiers"], obj.modifiers)

    obj_data["constraints"] = process_object_constraints(obj.constraints)
    obj_data["particle_systems"] = process_object_particle_systems(obj)

    process_animation_data(obj_data, obj, bpy.data.actions)

    # export custom properties
    obj_data["b4a_do_not_batch"] = obj.b4a_do_not_batch
    obj_data["b4a_dynamic_geometry"] = obj.b4a_dynamic_geometry
    obj_data["b4a_do_not_cull"] = obj.b4a_do_not_cull
    obj_data["b4a_disable_fogging"] = obj.b4a_disable_fogging
    obj_data["b4a_do_not_render"] = obj.b4a_do_not_render
    obj_data["b4a_shadow_cast"] = obj.b4a_shadow_cast
    obj_data["b4a_shadow_receive"] = obj.b4a_shadow_receive
    obj_data["b4a_reflexible"] = obj.b4a_reflexible
    obj_data["b4a_reflexible_only"] = obj.b4a_reflexible_only
    obj_data["b4a_reflective"] = obj.b4a_reflective
    obj_data["b4a_caustics"] = obj.b4a_caustics
    obj_data["b4a_wind_bending"] = obj.b4a_wind_bending
    obj_data["b4a_wind_bending_angle"] \
            = round_num(obj.b4a_wind_bending_angle, 1)
    obj_data["b4a_wind_bending_freq"] \
            = round_num(obj.b4a_wind_bending_freq, 2)
    obj_data["b4a_detail_bending_amp"] \
            = round_num(obj.b4a_detail_bending_amp, 4)
    obj_data["b4a_detail_bending_freq"] \
            = round_num(obj.b4a_detail_bending_freq, 3)
    obj_data["b4a_branch_bending_amp"] \
            = round_num(obj.b4a_branch_bending_amp, 4)
    obj_data["b4a_main_bend_stiffness_col"] \
            = obj.b4a_main_bend_stiffness_col

    detail_bend = obj.b4a_detail_bend_colors
    dct = obj_data["b4a_detail_bend_colors"] = OrderedDict()
    dct["leaves_stiffness_col"] = detail_bend.leaves_stiffness_col
    dct["leaves_phase_col"] = detail_bend.leaves_phase_col
    dct["overall_stiffness_col"] = detail_bend.overall_stiffness_col

    obj_data["b4a_lod_transition"] = round_num(obj.b4a_lod_transition, 3);
    obj_data["b4a_lod_distance"] = round_num(obj.b4a_lod_distance, 2)

    obj_data["lod_levels"] = process_object_lod_levels(obj)

    obj_data["b4a_proxy_inherit_anim"] = obj.b4a_proxy_inherit_anim

    obj_data["b4a_group_relative"] = obj.b4a_group_relative

    obj_data["b4a_selectable"] = obj.b4a_selectable
    obj_data["b4a_billboard"] = obj.b4a_billboard
    obj_data["b4a_billboard_geometry"] = obj.b4a_billboard_geometry

    gw_set = obj.b4a_glow_settings
    dct = obj_data["b4a_glow_settings"] = OrderedDict()
    dct["glow_duration"] = round_num(gw_set.glow_duration, 2)
    dct["glow_period"] = round_num(gw_set.glow_period, 2)
    dct["glow_relapses"] = gw_set.glow_relapses

    obj_data["b4a_use_default_animation"] = obj.b4a_use_default_animation
    obj_data["b4a_anim_behavior"] = obj.b4a_anim_behavior
    obj_data["b4a_animation_mixing"] = obj.b4a_animation_mixing
    obj_data["b4a_collision"] = obj.b4a_collision
    obj_data["b4a_collision_id"] = obj.b4a_collision_id

    obj_data["b4a_shadow_cast_only"] = obj.b4a_shadow_cast_only

    obj_data["b4a_vehicle"] = obj.b4a_vehicle
    if obj.b4a_vehicle:
        vh_set = obj.b4a_vehicle_settings
        dct = obj_data["b4a_vehicle_settings"] = OrderedDict()
        dct["name"] = vh_set.name
        dct["part"] = vh_set.part
        dct["suspension_rest_length"] = round_num(vh_set.suspension_rest_length, 3)
        dct["suspension_compression"] = round_num(vh_set.suspension_compression, 3)
        dct["suspension_stiffness"] = round_num(vh_set.suspension_stiffness, 3)
        dct["suspension_damping"] = round_num(vh_set.suspension_damping, 3)
        dct["wheel_friction"] = round_num(vh_set.wheel_friction, 3)
        dct["roll_influence"] = round_num(vh_set.roll_influence, 3)
        dct["max_suspension_travel_cm"] \
                = round_num(vh_set.max_suspension_travel_cm, 3)
        dct["force_max"] = round_num(vh_set.force_max, 3)
        dct["brake_max"] = round_num(vh_set.brake_max, 3)
        dct["steering_max"] = round_num(vh_set.steering_max, 3)
        dct["max_speed_angle"] = round_num(vh_set.max_speed_angle, 3)
        dct["delta_tach_angle"] = round_num(vh_set.delta_tach_angle, 3)
        dct["speed_ratio"] = round_num(vh_set.speed_ratio, 3)
        dct["steering_ratio"] = round_num(vh_set.steering_ratio, 3)
        dct["inverse_control"] = vh_set.inverse_control
        dct["floating_factor"] = round_num(vh_set.floating_factor, 3)
        dct["water_lin_damp"] = round_num(vh_set.water_lin_damp, 3)
        dct["water_rot_damp"] = round_num(vh_set.water_rot_damp, 3)
        dct["synchronize_position"] = vh_set.synchronize_position
    else:
        obj_data["b4a_vehicle_settings"] = None

    store_vehicle_integrity(obj)

    obj_data["b4a_character"] = obj.b4a_character
    if obj.b4a_character:
        ch_set = obj.b4a_character_settings
        dct = obj_data["b4a_character_settings"] = OrderedDict()
        dct["walk_speed"] = round_num(ch_set.walk_speed, 3)
        dct["run_speed"] = round_num(ch_set.run_speed, 3)
        dct["step_height"] = round_num(ch_set.step_height, 3)
        dct["jump_strength"] = round_num(ch_set.jump_strength, 3)
        dct["waterline"] = round_num(ch_set.waterline, 3)
    else:
        obj_data["b4a_character_settings"] = None

    obj_data["b4a_floating"] = obj.b4a_floating
    if obj.b4a_floating:
        fl_set = obj.b4a_floating_settings
        dct = obj_data["b4a_floating_settings"] = OrderedDict()
        dct["name"] = fl_set.name
        dct["part"] = fl_set.part
        dct["floating_factor"] = round_num(fl_set.floating_factor, 3)
        dct["water_lin_damp"] = round_num(fl_set.water_lin_damp, 3)
        dct["water_rot_damp"] = round_num(fl_set.water_rot_damp, 3)
        dct["synchronize_position"] = fl_set.synchronize_position
    else:
        obj_data["b4a_floating_settings"] = None

    obj_data["b4a_correct_bounding_offset"] = obj.b4a_correct_bounding_offset

    process_object_game_settings(obj_data, obj)
    process_object_pose(obj_data, obj, obj.pose)
    process_object_force_field(obj_data, obj.field)

    loc = obj.location
    obj_data["location"] = round_iterable([loc[0], loc[2], -loc[1]], 5)

    rot = get_rotation_quat(obj)
    obj_data["rotation_quaternion"] = round_iterable([rot[0], rot[1], rot[3], -rot[2]], 5)

    if not (obj_data["type"] == "MESH" and obj.b4a_apply_scale):
        sca = obj.scale
        obj_data["scale"] = round_iterable([sca[0], sca[2], sca[1]], 5)
    else:
        obj_data["scale"] = round_iterable([1.0, 1.0, 1.0], 5)

    _export_data["objects"].append(obj_data)
    _export_uuid_cache[obj_data["uuid"]] = obj_data
    _bpy_uuid_cache[obj_data["uuid"]] = obj
    check_object_data(obj_data, obj)

def get_rotation_quat(obj):
    if obj.rotation_mode == "AXIS_ANGLE":
        angle = obj.rotation_axis_angle[0]
        axis = obj.rotation_axis_angle[1:4]
        return mathutils.Quaternion(axis, angle)
    elif obj.rotation_mode == "QUATERNION":
        return obj.rotation_quaternion
    else:
        return obj.rotation_euler.to_quaternion()

def store_vehicle_integrity(obj):
    if obj.b4a_vehicle:
        if obj.b4a_vehicle_settings.name not in _vehicle_integrity:
            _vehicle_integrity[obj.b4a_vehicle_settings.name] = {
                "hull": None,
                "chassis": None,
                "bob": None,
                "wheel": None,
                "other": None
            }

        if obj.b4a_vehicle_settings.part == "HULL":
            _vehicle_integrity[obj.b4a_vehicle_settings.name]["hull"] = obj
        elif obj.b4a_vehicle_settings.part == "CHASSIS":
            _vehicle_integrity[obj.b4a_vehicle_settings.name]["chassis"] = obj
        elif obj.b4a_vehicle_settings.part == "BOB":
            _vehicle_integrity[obj.b4a_vehicle_settings.name]["bob"] = obj
        elif obj.b4a_vehicle_settings.part in ["WHEEL_FRONT_LEFT",
                "WHEEL_FRONT_RIGHT", "WHEEL_BACK_LEFT", "WHEEL_BACK_RIGHT"]:
            _vehicle_integrity[obj.b4a_vehicle_settings.name]["wheel"] = obj
        else:
            _vehicle_integrity[obj.b4a_vehicle_settings.name]["other"] = obj

def process_object_game_settings(obj_data, obj):
    game = obj.game

    dct = obj_data["game"] = OrderedDict()
    dct["physics_type"] = game.physics_type
    dct["use_ghost"] = game.use_ghost
    dct["use_sleep"] = game.use_sleep
    dct["mass"] = round_num(game.mass, 3)
    dct["velocity_min"] = round_num(game.velocity_min, 3)
    dct["velocity_max"] = round_num(game.velocity_max, 3)
    dct["damping"] = round_num(game.damping, 3)
    dct["rotation_damping"] = round_num(game.rotation_damping, 3)

    dct["lock_location_x"] = game.lock_location_x
    dct["lock_location_y"] = game.lock_location_y
    dct["lock_location_z"] = game.lock_location_z
    dct["lock_rotation_x"] = game.lock_rotation_x
    dct["lock_rotation_y"] = game.lock_rotation_y
    dct["lock_rotation_z"] = game.lock_rotation_z
    dct["collision_group"] = process_mask(game.collision_group)
    dct["collision_mask"] = process_mask(game.collision_mask)

    dct["use_collision_bounds"] = game.use_collision_bounds
    dct["collision_bounds_type"] = game.collision_bounds_type
    dct["use_collision_compound"] = game.use_collision_compound

def process_mask(bin_list):
    """Convert list of binaries to integer mask"""
    mask = 0
    for i in range(len(bin_list)):
        mask += int(bin_list[i]) * (2**i)

    return mask

# 3
def process_object_pose(obj_data, obj, pose):
    """export current pose"""

    obj_data["pose"] = None
    if pose:
        obj_data["pose"] = OrderedDict()
        obj_data["pose"]["bones"] = []

        pose_bones = pose.bones
        for pose_bone in pose_bones:

            pose_bone_data = OrderedDict()
            pose_bone_data["name"] = pose_bone.name

            # instead of link just provide index in armature
            pose_bone_data["bone"] = obj.data.bones.values().index(pose_bone.bone)

            # parent-child relationships
            parent_recursive = pose_bone.parent_recursive
            parent_recursive_indices = []

            parent_recursive_indices = [pose_bones.values().index(item) \
                    for item in parent_recursive]
            parent_recursive_indices = round_iterable(parent_recursive_indices)
            pose_bone_data["parent_recursive"] = parent_recursive_indices

            # MATH
            # L = armature bone "matrix_local"
            # B = pose bone "matrix_basis"
            # result = L * B * Li
            # skinned vertex = vertex * result

            # AXES CONVERTION
            # blender x - right, y - forward, z - up
            # opengl x - right, y - up, z - backward

            # R = mathutils.Matrix.Rotation(-math.pi / 2, 4, "X")
            # result = R * result * Ri = R * L * B * Li * Ri
            # each component can be separately converted because
            # R * L * B * Li * Ri = R * L * Ri * R * B * Ri * R * Li * Ri =
            # = (R * L * Ri) * (R * B * Ri) * (R * L * Ri)i
            # the latter because (A * B * C)i = (B * C)i * Ai = Ci * Bi * Ai

            # matrix_basis is pose transform relative to rest position
            # normally we get it from pose_bone.matrix_basis
            # but in order to bake inverse kinematics pose we
            # calculate "pseudo" matrix_basis from matrix_channel
            # which is pose accumulated through hierarchy
            # e.g. channel1 = rest0 * pose0 * rest0.inverted()
            #               * rest1 * pose1 * rest1.inverted()
            mch = pose_bone.matrix_channel

            # we need "own" matrix_channel component so remove parent if any
            parent = pose_bone.parent
            if parent:
                mch_par = parent.matrix_channel
                mch = mch_par.inverted() * mch

            # bone matrix in rest position (see armature section)
            ml = pose_bone.bone.matrix_local

            # finally get "pseudo" (i.e. baked) matrix basis by reverse operation
            mb = ml.inverted() * mch * ml

            # change axes from Blender to OpenGL
            m_rotX = mathutils.Matrix.Rotation(-math.pi / 2, 4, "X")
            m_rotXi = m_rotX.inverted()
            mb = m_rotX * mb * m_rotXi

            # flatten
            mb = matrix4x4_to_list(mb)
            pose_bone_data["matrix_basis"] = round_iterable(mb, 5)

            obj_data["pose"]["bones"].append(pose_bone_data)

def process_object_nla(nla_tracks_data, nla_tracks, actions):
    for track in nla_tracks:
        track_data = OrderedDict()
        track_data["name"] = track.name
        track_data["strips"] = []

        for strip in track.strips:
            strip_data = OrderedDict()
            strip_data["name"] = strip.name
            strip_data["type"] = strip.type
            strip_data["frame_start"] = round_num(strip.frame_start, 3)
            strip_data["frame_end"] = round_num(strip.frame_end, 3)
            strip_data["use_animated_time_cyclic"] \
                    = strip.use_animated_time_cyclic

            action = select_action(strip.action, actions)
            if action and do_export(action):
                strip_data["action"] = gen_uuid_obj(action)
            else:
                strip_data["action"] = None

            strip_data["action_frame_start"] = round_num(strip.action_frame_start, 3)
            strip_data["action_frame_end"] = round_num(strip.action_frame_end, 3)

            track_data["strips"].append(strip_data)

        nla_tracks_data.append(track_data)

def select_action(base_action, actions):
    if not base_action:
        return base_action

    # baked itself
    if anim_baker.has_baked_suffix(base_action):
        return base_action

    # search baked
    for action in actions:
        if action.name == (base_action.name + anim_baker.BAKED_SUFFIX):
            return action

    # not found
    return base_action

def process_object_force_field(obj_data, field):
    if field and field.type == 'WIND':
        dct = obj_data["field"] = OrderedDict()
        dct["type"] = field.type
        dct["strength"] = round_num(field.strength, 3)
        dct["seed"] = field.seed
    else:
        obj_data["field"] = None

def process_group(group, for_particles=False):
    if "export_done" in group and group["export_done"]:
        return
    group["export_done"] = True

    group_data = OrderedDict()
    group_data["name"] = group.name
    group_data["uuid"] = gen_uuid(group)

    # process group links
    group_data["objects"] = []
    for obj in group.objects:

        if for_particles:
            is_valid = particle_object_is_valid(obj)
        else:
            is_valid = object_is_valid(obj)

        if do_export(obj) and is_valid:
            group_data["objects"].append(gen_uuid_obj(obj))
            process_object(obj)

    _export_data["groups"].append(group_data)
    _export_uuid_cache[group_data["uuid"]] = group_data
    _bpy_uuid_cache[group_data["uuid"]] = group

def process_camera(camera):
    if "export_done" in camera and camera["export_done"]:
        return
    camera["export_done"] = True

    cam_data = OrderedDict()

    cam_data["name"] = camera.name
    cam_data["uuid"] = gen_uuid(camera)

    # 'PERSP' or 'ORTHO'
    cam_data["type"] = camera.type
    cam_data["angle"] = round_num(camera.angle, 6)
    cam_data["angle_y"] = round_num(camera.angle_y, 6)
    cam_data["ortho_scale"] = round_num(camera.ortho_scale, 3)
    cam_data["clip_start"] = round_num(camera.clip_start, 3)
    cam_data["clip_end"] = round_num(camera.clip_end, 3)
    cam_data["dof_distance"] = round_num(camera.dof_distance, 3)
    cam_data["b4a_dof_front"] = round_num(camera.b4a_dof_front, 3)
    cam_data["b4a_dof_rear"] = round_num(camera.b4a_dof_rear, 3)
    cam_data["b4a_dof_power"] = round_num(camera.b4a_dof_power, 2)
    cam_data["b4a_move_style"] = camera.b4a_move_style

    cam_data["b4a_use_distance_limits"] = camera.b4a_use_distance_limits
    cam_data["b4a_distance_min"] = round_num(camera.b4a_distance_min, 3)
    cam_data["b4a_distance_max"] = round_num(camera.b4a_distance_max, 3)

    cam_data["b4a_use_horizontal_clamping"] = camera.b4a_use_horizontal_clamping
    cam_data["b4a_rotation_left_limit"] \
            = round_num(camera.b4a_rotation_left_limit, 6)
    cam_data["b4a_rotation_right_limit"] \
            = round_num(camera.b4a_rotation_right_limit, 6)
    cam_data["b4a_horizontal_clamping_type"] \
            = camera.b4a_horizontal_clamping_type

    cam_data["b4a_use_vertical_clamping"] = camera.b4a_use_vertical_clamping
    cam_data["b4a_rotation_down_limit"] \
            = round_num(camera.b4a_rotation_down_limit, 6)
    cam_data["b4a_rotation_up_limit"] \
            = round_num(camera.b4a_rotation_up_limit, 6)
    cam_data["b4a_vertical_clamping_type"] \
            = camera.b4a_vertical_clamping_type

    # translate to b4a coordinates
    b4a_target = [camera.b4a_target[0], camera.b4a_target[2], \
            -camera.b4a_target[1]]
    cam_data["b4a_target"] = round_iterable(b4a_target, 3)

    # process camera links
    obj = camera.dof_object
    if obj and do_export(obj) and object_is_valid(obj):
        cam_data["dof_object"] = gen_uuid_obj(obj)
        process_object(obj)
    else:
        cam_data["dof_object"] = None

    _export_data["cameras"].append(cam_data)
    _export_uuid_cache[cam_data["uuid"]] = cam_data
    _bpy_uuid_cache[cam_data["uuid"]] = camera

def process_curve(curve):
    if "export_done" in curve and curve["export_done"]:
        return
    curve["export_done"] = True

    curve_data = OrderedDict()

    curve_data["name"] = curve.name
    curve_data["uuid"] = gen_uuid(curve)
    curve_data["dimensions"] = curve.dimensions

    curve_data["splines"] = []

    for spline in curve.splines:
        spline_data = OrderedDict()

        spline_data["bezier_points"] = []

        spline_data["use_bezier_u"] = spline.use_bezier_u
        spline_data["use_cyclic_u"] = spline.use_cyclic_u
        spline_data["use_endpoint_u"] = spline.use_endpoint_u
        spline_data["order_u"] = spline.order_u

        points = []
        for point in spline.points:
            points.append(point.co[0])
            points.append(point.co[2])
            points.append(-point.co[1])
            points.append(point.tilt)
            points.append(point.co[3])

        spline_data["points"] = round_iterable(points, 5)
        spline_data["type"] = spline.type

        curve_data["splines"].append(spline_data)

    _export_data["curves"].append(curve_data)
    _export_uuid_cache[curve_data["uuid"]] = curve_data
    _bpy_uuid_cache[curve_data["uuid"]] = curve

def process_lamp(lamp):
    if "export_done" in lamp and lamp["export_done"]:
        return
    lamp["export_done"] = True

    lamp_data = OrderedDict()

    lamp_data["name"] = lamp.name
    lamp_data["uuid"] = gen_uuid(lamp)
    lamp_data["type"] = lamp.type
    lamp_data["energy"] = round_num(lamp.energy, 3)
    lamp_data["distance"] = round_num(lamp.distance, 3)

    lamp_data["use_diffuse"] = lamp.use_diffuse
    lamp_data["use_specular"] = lamp.use_specular

    if (lamp.type == "POINT" or lamp.type == "SPOT"):
        lamp_data["falloff_type"] = lamp.falloff_type
    else:
        lamp_data["falloff_type"] = None

    if (lamp.type == "SPOT"):
        lamp_data["spot_size"] = round_num(lamp.spot_size, 5)
        lamp_data["spot_blend"] = round_num(lamp.spot_blend, 3)
    else:
        lamp_data["spot_size"] = None
        lamp_data["spot_blend"] = None

    lamp_data["b4a_generate_shadows"] = lamp.b4a_generate_shadows
    lamp_data["b4a_dynamic_intensity"] = lamp.b4a_dynamic_intensity
    lamp_data["color"] = round_iterable(lamp.color, 4)

    _export_data["lamps"].append(lamp_data)
    _export_uuid_cache[lamp_data["uuid"]] = lamp_data
    _bpy_uuid_cache[lamp_data["uuid"]] = lamp

def process_material(material):
    _curr_material_stack.append(material)
    material["in_use"] = True
    mat_data = OrderedDict()

    mat_data["name"] = material.name
    mat_data["uuid"] = gen_uuid(material)

    mat_data["use_nodes"] = material.use_nodes

    mat_data["diffuse_color"] = round_iterable(material.diffuse_color, 4)
    mat_data["diffuse_shader"] = material.diffuse_shader
    mat_data["roughness"] = round_num(material.roughness, 3)
    mat_data["diffuse_fresnel"] = round_num(material.diffuse_fresnel, 3)
    mat_data["diffuse_fresnel_factor"] \
            = round_num(material.diffuse_fresnel_factor, 3)
    mat_data["diffuse_intensity"] = round_num(material.diffuse_intensity, 3)
    mat_data["alpha"] = round_num(material.alpha, 4)
    mat_data["specular_alpha"] = round_num(material.specular_alpha, 4)

    raytrace_transparency = material.raytrace_transparency
    dct = mat_data["raytrace_transparency"] = OrderedDict()
    dct["fresnel"] = round_num(raytrace_transparency.fresnel, 4)
    dct["fresnel_factor"] = round_num(raytrace_transparency.fresnel_factor, 4)

    raytrace_mirror = material.raytrace_mirror
    dct = mat_data["raytrace_mirror"] = OrderedDict()
    dct["reflect_factor"] = round_num(raytrace_mirror.reflect_factor, 4)
    dct["fresnel"] = round_num(raytrace_mirror.fresnel, 4)
    dct["fresnel_factor"] = round_num(raytrace_mirror.fresnel_factor, 4)

    mat_data["specular_color"] = round_iterable(material.specular_color, 4)
    mat_data["specular_intensity"] = round_num(material.specular_intensity, 4)
    mat_data["specular_shader"] = material.specular_shader
    mat_data["specular_hardness"] = round_num(material.specular_hardness, 4)
    mat_data["specular_slope"] = round_num(material.specular_slope, 4)
    mat_data["specular_toon_size"] = round_num(material.specular_toon_size, 4)
    mat_data["specular_toon_smooth"] = round_num(material.specular_toon_smooth, 4)
    mat_data["emit"] = round_num(material.emit, 3)
    mat_data["ambient"] = round_num(material.ambient, 3)
    mat_data["use_vertex_color_paint"] = material.use_vertex_color_paint

    # export custom properties
    mat_data["b4a_water"] = material.b4a_water
    mat_data["b4a_water_shore_smoothing"] = material.b4a_water_shore_smoothing
    mat_data["b4a_water_absorb_factor"] \
            = round_num(material.b4a_water_absorb_factor, 3)
    mat_data["b4a_water_dynamic"] = material.b4a_water_dynamic
    mat_data["b4a_waves_height"] = round_num(material.b4a_waves_height, 3)
    mat_data["b4a_waves_length"] = round_num(material.b4a_waves_length, 3)
    mat_data["b4a_generated_mesh"] = material.b4a_generated_mesh
    mat_data["b4a_water_num_cascads"] \
            = round_num(material.b4a_water_num_cascads, 1)
    mat_data["b4a_water_subdivs"] = round_num(material.b4a_water_subdivs, 1)
    mat_data["b4a_water_detailed_dist"] \
            = round_num(material.b4a_water_detailed_dist, 1)
    mat_data["b4a_water_fog_color"] \
            = round_iterable(material.b4a_water_fog_color, 4)
    mat_data["b4a_water_fog_density"] \
            = round_num(material.b4a_water_fog_density, 4)
    mat_data["b4a_foam_factor"] = round_num(material.b4a_foam_factor, 3)
    mat_data["b4a_shallow_water_col"] \
            = round_iterable(material.b4a_shallow_water_col, 4)
    mat_data["b4a_shore_water_col"] \
            = round_iterable(material.b4a_shore_water_col, 4)
    mat_data["b4a_shallow_water_col_fac"] \
            = round_num(material.b4a_shallow_water_col_fac, 3)
    mat_data["b4a_shore_water_col_fac"] \
            = round_num(material.b4a_shore_water_col_fac, 3)

    mat_data["b4a_water_dst_noise_scale0"] \
            = round_num(material.b4a_water_dst_noise_scale0, 3)
    mat_data["b4a_water_dst_noise_scale1"] \
            = round_num(material.b4a_water_dst_noise_scale1, 3)
    mat_data["b4a_water_dst_noise_freq0"] \
            = round_num(material.b4a_water_dst_noise_freq0, 3)
    mat_data["b4a_water_dst_noise_freq1"] \
            = round_num(material.b4a_water_dst_noise_freq1, 3)
    mat_data["b4a_water_dir_min_shore_fac"] \
            = round_num(material.b4a_water_dir_min_shore_fac, 3)
    mat_data["b4a_water_dir_freq"] = round_num(material.b4a_water_dir_freq, 3)
    mat_data["b4a_water_dir_noise_scale"] \
            = round_num(material.b4a_water_dir_noise_scale, 3)
    mat_data["b4a_water_dir_noise_freq"] \
            = round_num(material.b4a_water_dir_noise_freq, 3)
    mat_data["b4a_water_dir_min_noise_fac"] \
            = round_num(material.b4a_water_dir_min_noise_fac, 3)
    mat_data["b4a_water_dst_min_fac"] \
            = round_num(material.b4a_water_dst_min_fac, 3)
    mat_data["b4a_water_waves_hor_fac"] \
            = round_num(material.b4a_water_waves_hor_fac, 3)
    mat_data["b4a_water_sss_strength"] \
            = round_num(material.b4a_water_sss_strength, 3)
    mat_data["b4a_water_sss_width"] = round_num(material.b4a_water_sss_width, 3)
    mat_data["b4a_terrain"] = material.b4a_terrain
    mat_data["b4a_dynamic_grass_size"] = material.b4a_dynamic_grass_size
    mat_data["b4a_dynamic_grass_color"] = material.b4a_dynamic_grass_color

    mat_data["b4a_collision"] = material.b4a_collision
    mat_data["b4a_use_ghost"] = material.b4a_use_ghost
    mat_data["b4a_collision_id"] = material.b4a_collision_id
    mat_data["b4a_collision_group"] = process_mask(material.b4a_collision_group)
    mat_data["b4a_collision_mask"] = process_mask(material.b4a_collision_mask)
    mat_data["b4a_double_sided_lighting"] = material.b4a_double_sided_lighting
    mat_data["b4a_wettable"] = material.b4a_wettable
    mat_data["b4a_refractive"] = material.b4a_refractive
    mat_data["b4a_refr_bump"] = material.b4a_refr_bump

    mat_data["b4a_render_above_all"] = material.b4a_render_above_all

    process_material_physics(mat_data, material)

    mat_data["type"] = material.type

    mat_halo = material.halo

    mat_data["b4a_halo_sky_stars"] = material.b4a_halo_sky_stars
    mat_data["b4a_halo_stars_blend_height"] \
            = material.b4a_halo_stars_blend_height
    mat_data["b4a_halo_stars_min_height"] \
            = material.b4a_halo_stars_min_height

    dct = mat_data["halo"] = OrderedDict()
    dct["hardness"] = mat_halo.hardness
    dct["size"] = round_num(mat_halo.size, 3)

    # NOTE: Halo rings color in blender 2.68 is equal to mirror color
    dct["b4a_halo_rings_color"] = round_iterable(material.mirror_color, 4)
    # NOTE: Halo lines color in blender 2.68 is equal to specular color
    dct["b4a_halo_lines_color"] = round_iterable(material.specular_color, 4)

    dct["ring_count"] =  mat_halo.ring_count if mat_halo.use_ring else 0
    dct["line_count"] =  mat_halo.line_count if mat_halo.use_lines else 0
    dct["star_tip_count"] \
            =  mat_halo.star_tip_count if mat_halo.use_star else 0

    mat_data["use_transparency"] = material.use_transparency
    mat_data["use_shadeless"] = material.use_shadeless
    mat_data["offset_z"] = round_num(material.offset_z, 2)

    game_settings = material.game_settings
    dct = mat_data["game_settings"] = OrderedDict()
    dct["alpha_blend"] = game_settings.alpha_blend
    dct["use_backface_culling"] = game_settings.use_backface_culling

    # process material links
    if mat_data["use_nodes"]:
        process_node_tree(mat_data, material)
    else:
        mat_data["node_tree"] = None
    process_material_texture_slots(mat_data, material)

    need_append = not ("export_done" in material and material["export_done"])

    if need_append:
        material["export_done"] = True
        mat_data["uuid"] = gen_uuid(material)

        _export_data["materials"].append(mat_data)
        _export_uuid_cache[mat_data["uuid"]] = mat_data
        _bpy_uuid_cache[mat_data["uuid"]] = material
    _curr_material_stack.pop()


def process_material_physics(mat_data, material):
    phy = material.physics

    dct = mat_data["physics"] = OrderedDict()
    dct["friction"] = round_num(phy.friction, 3)
    dct["elasticity"] = round_num(phy.elasticity, 3)

def process_texture(texture):
    if "export_done" in texture and texture["export_done"]:
        return
    texture["export_done"] = True

    tex_data = OrderedDict()

    tex_data["name"] = texture.name
    tex_data["uuid"] = gen_uuid(texture)
    tex_data["type"] = texture.type

    tex_data["b4a_render_scene"] = texture.b4a_render_scene

    if hasattr(texture, "extension"):
        tex_data["extension"] = texture.extension
    else:
        tex_data["extension"] = None

    tex_data["b4a_use_map_parallax"] = texture.b4a_use_map_parallax
    tex_data["b4a_parallax_scale"] = round_num(texture.b4a_parallax_scale, 3)
    tex_data["b4a_parallax_steps"] = round_num(texture.b4a_parallax_steps, 1)
    tex_data["b4a_parallax_lod_dist"] = round_num(texture.b4a_parallax_lod_dist, 3)
    tex_data["b4a_water_foam"] = texture.b4a_water_foam
    tex_data["b4a_foam_uv_freq"] = round_iterable(texture.b4a_foam_uv_freq, 3)

    tex_data["b4a_foam_uv_magnitude"] \
            = round_iterable(texture.b4a_foam_uv_magnitude, 3)
    tex_data["b4a_shore_dist_map"] = texture.b4a_shore_dist_map
    tex_data["b4a_anisotropic_filtering"] = texture.b4a_anisotropic_filtering
    tex_data["b4a_shore_boundings"] \
            = round_iterable(texture.b4a_shore_boundings, 3)
    tex_data["b4a_max_shore_dist"] = round_num(texture.b4a_max_shore_dist, 3)
    tex_data["b4a_uv_velocity_trans"] \
            = round_iterable(texture.b4a_uv_velocity_trans, 3)

    tex_data["b4a_disable_compression"] = texture.b4a_disable_compression
    tex_data["b4a_use_as_skydome"] = False
    tex_data["b4a_use_as_environment_lighting"] = False
    if texture.b4a_use_sky == "SKYDOME" or texture.b4a_use_sky == "BOTH":
        tex_data["b4a_use_as_skydome"] = True
    if texture.b4a_use_sky == "ENVIRONMENT_LIGHTING" or texture.b4a_use_sky == "BOTH":
        tex_data["b4a_use_as_environment_lighting"] = True

    # process texture links
    if hasattr(texture, "image"):
        image = texture.image
        if not image:
            raise MaterialError("No image in the \"" + texture.name + "\" texture.")
        if do_export(image):
            tex_data["image"] = gen_uuid_obj(image)
            process_image(image)
    else:
        tex_data["image"] = None


    if texture.type == 'VORONOI':
        tex_data["noise_intensity"] = round_num(texture.noise_intensity, 3)
        tex_data["noise_scale"] = round_num(texture.noise_scale, 2)
    else:
        tex_data["noise_intensity"] = None
        tex_data["noise_scale"] = None

    use_ramp = texture.use_color_ramp
    tex_data["use_color_ramp"] = use_ramp

    if use_ramp:
        process_color_ramp(tex_data, texture.color_ramp)
    else:
        tex_data["color_ramp"] = None

    _export_data["textures"].append(tex_data)
    _export_uuid_cache[tex_data["uuid"]] = tex_data
    _bpy_uuid_cache[tex_data["uuid"]] = texture

def process_color_ramp(tex_data, ramp):
    tex_data["color_ramp"] = OrderedDict()
    tex_data["color_ramp"]["elements"] = []

    for el in ramp.elements:
        el_data = OrderedDict()
        el_data["position"] = round_num(el.position, 3)
        el_data["color"] = round_iterable(el.color, 3)
        tex_data["color_ramp"]["elements"].append(el_data)

def process_image(image):
    if "export_done" in image and image["export_done"]:
        return
    image["export_done"] = True

    image_data = OrderedDict()

    image_data["name"] = image.name
    image_data["uuid"] = gen_uuid(image)

    if image.packed_file is not None:
        packed_data = b4a_bin.get_packed_data(image.packed_file.as_pointer())
        packed_file_path = get_json_relative_filepath(
                packed_resource_get_unique_name(image))

        # NOTE: fix image path without extension, e.g. custom image created in
        # Blender and packed as PNG
        if os.path.splitext(packed_file_path)[1] == "":
            packed_file_path += "." + image.file_format.lower()

        _packed_files_data[packed_file_path] = packed_data
        image_data["filepath"] = packed_file_path
    else:
        image_data["filepath"] = get_filepath(image)

    image_data["size"] = list(image.size)
    image_data["source"] = image.source

    _export_data["images"].append(image_data)
    _export_uuid_cache[image_data["uuid"]] = image_data
    _bpy_uuid_cache[image_data["uuid"]] = image

def get_filepath(comp):
    path_b = comp.filepath.replace("//", "")

    # path to component relative to BLEND
    if comp.library:
        path_lib = bpy.path.abspath(comp.library.filepath).replace("//", "")
        path_lib_dir = os.path.dirname(path_lib)
        path_b_abs = os.path.join(path_lib_dir, path_b)
        path_b = bpy.path.relpath(path_b_abs).replace("//", "")

    return get_json_relative_filepath(path_b)

def get_json_relative_filepath(path):
    # absolute path to exported JSON
    path_exp = _export_filepath.replace("//", "")
    # absolute path to resource
    path_res = os.path.normpath(os.path.join(\
            os.path.dirname(bpy.data.filepath), path))
    # path to resource relative to JSON
    try:
        path_relative = os.path.relpath(path_res, os.path.dirname(path_exp))
    except ValueError as exp:
        _file_error = exp
        raise FileError("Loading of resources from different disk is forbidden")
    # clean
    return guard_slashes(os.path.normpath(path_relative))


def process_mesh(mesh, obj_user):
    if "export_done" in mesh and mesh["export_done"]:
        return

    global _curr_mesh
    global _fallback_material
    _curr_mesh = mesh

    mesh["export_done"] = True

    # update needed from bmesh introduction (blender >= 2.63)
    # also note change faces -> tessfaces, uv_textures -> tessface_uv_textures
    mesh.update(calc_tessface=True)

    mesh_data = OrderedDict()
    mesh_data["name"] = mesh.name
    mesh_data["uuid"] = gen_uuid(mesh)

    # process mesh links
    # faces' material_index'es correspond to
    # blender saves pointers to materials section of root
    # so we will save uuids here
    mesh_data["materials"] = []
    for material in mesh.materials:
        # None for empty slot
        if not material:
            raise ExportError("Incomplete mesh", mesh, "Material slot is empty")

        if do_export(material):

            try:
                process_material(material)
                mesh_data["materials"].append(gen_uuid_obj(material))
            except MaterialError as ex:
                if not _fallback_material:
                    _fallback_material = bpy.data.materials.new("FALLBACK_MATERIAL")
                    _fallback_material.diffuse_color = (1,0,1)
                    _fallback_material.use_shadeless = True
                    process_material(_fallback_material)

                mesh_data["materials"].append(gen_uuid_obj(_fallback_material))
                err(str(ex) + " Material: " + "\"" + _curr_material_stack[-1].name + "\".")

    # process object's props
    process_mesh_vertex_anim(mesh_data, obj_user)
    process_mesh_vertex_groups(mesh_data, obj_user)

    mesh_data["uv_textures"] = []
    if mesh.uv_textures:
        # export 2 uv_textures only
        mesh_uv_count = len(mesh.uv_textures)
        if mesh_uv_count > 2:
            warn("Only 2 UV textures are allowed for a mesh. The mesh has " + str(mesh_uv_count) + " UVs.")
            mesh_data["uv_textures"].append(mesh.uv_textures[0].name)
            mesh_data["uv_textures"].append(mesh.uv_textures[1].name)
        else:
            for uv_texture in mesh.uv_textures:
                mesh_data["uv_textures"].append(uv_texture.name)

    active_vc = mesh_get_active_vc(mesh)
    if active_vc:
        mesh_data["active_vcol_name"] = active_vc.name
    else:
        mesh_data["active_vcol_name"] = None

    mesh_data["submeshes"] = []

    obj_ptr = obj_user.as_pointer()
    vertex_animation = bool(obj_user.b4a_loc_export_vertex_anim)
    edited_normals = bool(obj_user.b4a_export_edited_normals)
    vertex_groups = bool(obj_user.vertex_groups)
    vertex_colors = bool(mesh.vertex_colors)

    if obj_user.b4a_apply_scale:
        sca = obj_user.scale
        for v_index in range(len(mesh.vertices)):
            vert = mesh.vertices[v_index]
            vert.co.x = vert.co.x * sca[0]
            vert.co.y = vert.co.y * sca[1]
            vert.co.z = vert.co.z * sca[2]

    mesh_ptr = mesh.as_pointer()
    
def get_mat_vc_channel_usage(mesh, mat_index, obj_user):
    vc_channel_usage = {}

    if mesh.vertex_colors:
        mat = mesh.materials[mat_index] if mat_index >= 0 else None

        nodes_usage = vc_channel_nodes_usage(mat)
        dyn_grass_usage = vc_channel_dyn_grass_usage(mat)
        bending_usage = vc_channel_bending_usage(obj_user)
        color_paint_usage = vc_channel_color_paint_usage(mesh, mat)
        psys_inheritance_usage = vc_channel_psys_inheritance(obj_user)

        usage_data = {
            "nodes": nodes_usage,
            "dyn_grass": dyn_grass_usage,
            "bending": bending_usage,
            "color_paint": color_paint_usage,
            "psys_inheritance": psys_inheritance_usage
        }

        for usage_type in usage_data:
            for vc_name in usage_data[usage_type]:
                mask = usage_data[usage_type][vc_name]
                if vc_name in vc_channel_usage:
                    vc_channel_usage[vc_name] |= mask
                else:
                    vc_channel_usage[vc_name] = mask
    return vc_channel_usage

def vc_channel_nodes_usage(mat):
    vc_nodes_usage = {}

    if mat is not None and mat.node_tree is not None:
        separate_rgb_out = {}
        geometry_vcols = {}

        for link in mat.node_tree.links:
            if link.from_node.bl_idname == "ShaderNodeGeometry" \
                    and link.from_socket.identifier == "Vertex Color":
                vcol_name = link.from_node.color_layer
                if vcol_name:
                    to_name = link.to_node.name
                    if vcol_name not in geometry_vcols:
                        geometry_vcols[vcol_name] = []
                    if to_name not in geometry_vcols[vcol_name]:
                        geometry_vcols[vcol_name].append(to_name)

            elif link.from_node.bl_idname == "ShaderNodeSeparateRGB" \
                    and link.from_socket.identifier in "RGB":
                seprgb_name = link.from_node.name
                mask = rgb_channels_to_mask(link.from_socket.identifier)
                if seprgb_name in separate_rgb_out:
                    separate_rgb_out[seprgb_name] |= mask
                else:
                    separate_rgb_out[seprgb_name] = mask

        for vcol_name in geometry_vcols:
            for to_name in geometry_vcols[vcol_name]:
                if to_name in separate_rgb_out:
                    mask = separate_rgb_out[to_name]
                else:
                    mask = rgb_channels_to_mask("RGB")
                if vcol_name in vc_nodes_usage:
                    vc_nodes_usage[vcol_name] |= mask
                else:
                    vc_nodes_usage[vcol_name] = mask

    return vc_nodes_usage

def vc_channel_dyn_grass_usage(mat):
    vc_dyn_grass_usage = {}

    if mat is not None and mat.b4a_terrain:
        grass_col = mat.b4a_dynamic_grass_color
        grass_size_col = mat.b4a_dynamic_grass_size
        if grass_col:
            vc_dyn_grass_usage[grass_col] = rgb_channels_to_mask("RGB")
        if grass_size_col:
            if grass_size_col in vc_dyn_grass_usage:
                vc_dyn_grass_usage[grass_size_col] |= rgb_channels_to_mask("R")
            else:
                vc_dyn_grass_usage[grass_size_col] = rgb_channels_to_mask("R")

    return vc_dyn_grass_usage

def vc_channel_bending_usage(obj):
    vc_bending_usage = {}
    if obj.b4a_wind_bending:
        main_stiff = obj.b4a_main_bend_stiffness_col
        leaves_stiff = obj.b4a_detail_bend_colors.leaves_stiffness_col
        leaves_phase = obj.b4a_detail_bend_colors.leaves_phase_col
        overall_stiff = obj.b4a_detail_bend_colors.overall_stiffness_col

        if main_stiff:
            if main_stiff in vc_bending_usage:
                vc_bending_usage[main_stiff] |= rgb_channels_to_mask("R")
            else:
                vc_bending_usage[main_stiff] = rgb_channels_to_mask("R")
        if leaves_stiff:
            if leaves_stiff in vc_bending_usage:
                vc_bending_usage[leaves_stiff] |= rgb_channels_to_mask("R")
            else:
                vc_bending_usage[leaves_stiff] = rgb_channels_to_mask("R")
        if leaves_phase:
            if leaves_phase in vc_bending_usage:
                vc_bending_usage[leaves_phase] |= rgb_channels_to_mask("G")
            else:
                vc_bending_usage[leaves_phase] = rgb_channels_to_mask("G")
        if overall_stiff:
            if overall_stiff in vc_bending_usage:
                vc_bending_usage[overall_stiff] |= rgb_channels_to_mask("B")
            else:
                vc_bending_usage[overall_stiff] = rgb_channels_to_mask("B")
    return vc_bending_usage

def vc_channel_color_paint_usage(mesh, mat):
    vc_cpaint_usage = {}

    if mat is not None and mat.use_vertex_color_paint and len(mesh.vertex_colors):
        vc_active = mesh_get_active_vc(mesh)
        if vc_active:
            vc_cpaint_usage[vc_active.name] = rgb_channels_to_mask("RGB")

    return vc_cpaint_usage

def vc_channel_psys_inheritance(obj):
    vc_psys_inheritance = {}

    # NOTE: export vertex color 'from_name' fully on emitter,
    # don't consider particles on this stage
    for psys in obj.particle_systems:
        pset = psys.settings
        vc_from_name = pset.b4a_vcol_from_name
        vc_to_name = pset.b4a_vcol_to_name

        if vc_from_name and vc_to_name:
            vc_psys_inheritance[vc_from_name] = rgb_channels_to_mask("RGB")

    return vc_psys_inheritance

def rgb_channels_to_mask(channel_name):
    mask = 0b000
    if "R" in channel_name:
        mask |= 0b100
    if "G" in channel_name:
        mask |= 0b010
    if "B" in channel_name:
        mask |= 0b001
    return mask

def export_submesh(mesh, mesh_ptr, obj_user, obj_ptr, mat_index, disab_flat, \
        vertex_animation, edited_normals, vertex_groups, vertex_colors, \
        bounding_data):

    if edited_normals and len(mesh.vertices) \
            != len(obj_user.b4a_vertex_normal_list):
        raise ExportError("Wrong edited normals count", mesh, \
                "It doesn't match with mesh vertices count")

    if vertex_animation:
        if len(obj_user.b4a_vertex_anim) == 0:
            raise ExportError("Incorrect vertex animation", mesh, \
                    "Object has no vertex animation")
        else:
            for anim in obj_user.b4a_vertex_anim:
                if len(anim.frames) == 0:
                    raise ExportError("Incorrect vertex animation", mesh, \
                            "Unbaked \"" + anim.name + "\" vertex animation")
                elif len(mesh.vertices) != len(anim.frames[0].vertices):
                    raise ExportError("Wrong vertex animation vertices count", \
                            mesh, "It doesn't match with the mesh vertices " +
                            "count for \"" + anim.name + "\"")

    is_degenerate_mesh = not bool(max( \
            abs(bounding_data["max_x"] - bounding_data["min_x"]), \
            abs(bounding_data["max_y"] - bounding_data["min_y"]), \
            abs(bounding_data["max_z"] - bounding_data["min_z"])))

    vc_channel_usage = get_mat_vc_channel_usage(mesh, mat_index, obj_user)
    vc_mask_buffer = bytearray()

    for vc in mesh.vertex_colors:
        if vc.name in vc_channel_usage:
            vc_mask_buffer.append(vc_channel_usage[vc.name])
        else:
            vc_mask_buffer.append(0b000)

    try:
        submesh = b4a_bin.export_submesh(mesh_ptr, obj_ptr, mat_index, \
                disab_flat, vertex_animation, edited_normals, vertex_groups, \
                vertex_colors, vc_mask_buffer, is_degenerate_mesh)
    except Exception as ex:
        raise ExportError("Incorrect mesh", mesh, str(ex))

    submesh_data = OrderedDict()
    submesh_data["base_length"] = submesh["base_length"]

    int_props = ["indices"]
    for prop_name in int_props:
        if prop_name in submesh:
            if len(submesh[prop_name]):
                submesh_data[prop_name] = [
                    len(_bpy_bindata_int) // BINARY_INT_SIZE,
                    len(submesh[prop_name]) // BINARY_INT_SIZE
                ]
                _bpy_bindata_int.extend(submesh[prop_name])
            else:
                submesh_data[prop_name] = [0, 0]

    short_props = ["normal", "tangent"]
    for prop_name in short_props:
        if prop_name in submesh:
            if len(submesh[prop_name]):
                submesh_data[prop_name] = [
                    len(_bpy_bindata_short) // BINARY_SHORT_SIZE,
                    len(submesh[prop_name]) // BINARY_SHORT_SIZE
                ]
                _bpy_bindata_short.extend(submesh[prop_name])
            else:
                submesh_data[prop_name] = [0, 0]

    ushort_props = ["color", "group"]
    for prop_name in ushort_props:
        if prop_name in submesh:
            if len(submesh[prop_name]):
                submesh_data[prop_name] = [
                    len(_bpy_bindata_ushort) // BINARY_SHORT_SIZE,
                    len(submesh[prop_name]) // BINARY_SHORT_SIZE
                ]
                _bpy_bindata_ushort.extend(submesh[prop_name])
            else:
                submesh_data[prop_name] = [0, 0]

    float_props = ["position", "texcoord", "texcoord2"]
    for prop_name in float_props:
        if prop_name in submesh:
            if len(submesh[prop_name]):
                submesh_data[prop_name] = [
                    len(_bpy_bindata_float) // BINARY_FLOAT_SIZE,
                    len(submesh[prop_name]) // BINARY_FLOAT_SIZE
                ]
                _bpy_bindata_float.extend(submesh[prop_name])
            else:
                submesh_data[prop_name] = [0, 0]

    submesh_data["vertex_colors"] = []
    if mesh.vertex_colors:
        for color_layer in mesh.vertex_colors:
            if color_layer.name in vc_channel_usage:
                col_layer_data = OrderedDict()
                col_layer_data["name"] = color_layer.name
                col_layer_data["mask"] = vc_channel_usage[color_layer.name]
                submesh_data["vertex_colors"].append(col_layer_data)

    return submesh_data

def find_material_mesh(material, meshes):
    """unused"""
    """Find material user"""

    for mesh in meshes:
        mats = mesh.materials
        for mat in mats:
            if mat == material:
                return mesh

    # not found
    return None

def process_mesh_vertex_anim(mesh_data, obj_user):
    """Vertex animation metadata"""
    mesh_data["b4a_vertex_anim"] = []

    if obj_user and obj_user.b4a_loc_export_vertex_anim:
        for va_item in obj_user.b4a_vertex_anim:
            # prevent storage of non-baked animation ("Empty")
            if not va_item.frames:
                continue

            va_item_data = OrderedDict()
            va_item_data["name"] = va_item.name
            va_item_data["frame_start"] = va_item.frame_start
            va_item_data["frame_end"] = va_item.frame_end
            va_item_data["averaging"] = va_item.averaging
            va_item_data["averaging_interval"] = va_item.averaging_interval
            va_item_data["allow_nla"] = va_item.allow_nla

            mesh_data["b4a_vertex_anim"].append(va_item_data)

def process_mesh_vertex_groups(mesh_data, obj_user):
    """Only groups metadata exported here"""
    mesh_data["vertex_groups"] = []

    if obj_user and obj_user.vertex_groups:
        for vertex_group in obj_user.vertex_groups:
            vertex_group_data = OrderedDict()
            vertex_group_data["name"] = vertex_group.name
            vertex_group_data["index"] = vertex_group.index

            mesh_data["vertex_groups"].append(vertex_group_data)

def process_armature(armature):
    if "export_done" in armature and armature["export_done"]:
        return
    armature["export_done"] = True

    arm_data = OrderedDict()

    arm_data["name"] = armature.name
    arm_data["uuid"] = gen_uuid(armature)

    arm_data["bones"] = []
    bones = armature.bones
    for bone in bones:
        bone_data = OrderedDict()

        bone_data["name"] = bone.name

        # in bone space
        head = [bone.head[0], bone.head[2], -bone.head[1]]
        tail = [bone.tail[0], bone.tail[2], -bone.tail[1]]

        bone_data["head"] = round_iterable(head, 5)
        bone_data["tail"] = round_iterable(tail, 5)

        # in armature space
        hl = [bone.head_local[0], bone.head_local[2], -bone.head_local[1]]
        tl = [bone.tail_local[0], bone.tail_local[2], -bone.tail_local[1]]

        bone_data["head_local"] = round_iterable(hl, 5)
        bone_data["tail_local"] = round_iterable(tl, 5)

        # Bone Armature-Relative Matrix
        ml = bone.matrix_local

        # change axes from Blender to OpenGL
        # see pose section for detailed math
        m_rotX = mathutils.Matrix.Rotation(-math.pi / 2, 4, "X")
        ml = m_rotX * ml * m_rotX.inverted()

        # this line is correct if there is no axes convertion
        # for pose/animation (see pose section)
        #ml = m_rotX * ml

        # flatten
        ml = matrix4x4_to_list(ml)
        bone_data["matrix_local"] = round_iterable(ml, 5)

        arm_data["bones"].append(bone_data)

    _export_data["armatures"].append(arm_data)
    _export_uuid_cache[arm_data["uuid"]] = arm_data
    _bpy_uuid_cache[arm_data["uuid"]] = armature

def process_speaker(speaker):
    if "export_done" in speaker and speaker["export_done"]:
        return
    speaker["export_done"] = True

    spk_data = OrderedDict()

    spk_data["name"] = speaker.name
    spk_data["uuid"] = gen_uuid(speaker)

    sound = speaker.sound
    if sound:
        spk_data["sound"] = gen_uuid_obj(sound)
        process_sound(sound)
    else:
        spk_data["sound"] = None

    process_animation_data(spk_data, speaker, bpy.data.actions)

    # distance attenuation params
    spk_data["attenuation"] = round_num(speaker.attenuation, 3)
    spk_data["distance_reference"] = round_num(speaker.distance_reference, 3)

    dmax = speaker.distance_max
    if dmax > SPKDISTMAX:
        dmax = SPKDISTMAX

    spk_data["distance_max"] = round_num(dmax, 3)

    # spatialization params
    spk_data["cone_angle_inner"] = round_num(speaker.cone_angle_inner, 3)
    spk_data["cone_angle_outer"] = round_num(speaker.cone_angle_outer, 3)
    spk_data["cone_volume_outer"] = round_num(speaker.cone_volume_outer, 3)

    # common params
    spk_data["pitch"] = round_num(speaker.pitch, 3)
    spk_data["muted"] = speaker.muted
    spk_data["volume"] = round_num(speaker.volume, 3)

    # custom params
    spk_data["b4a_behavior"] = speaker.b4a_behavior
    spk_data["b4a_disable_doppler"] = speaker.b4a_disable_doppler
    spk_data["b4a_cyclic_play"] = speaker.b4a_cyclic_play
    spk_data["b4a_delay"] = round_num(speaker.b4a_delay, 3)
    spk_data["b4a_delay_random"] = round_num(speaker.b4a_delay_random, 3)
    spk_data["b4a_volume_random"] = round_num(speaker.b4a_volume_random, 3)
    spk_data["b4a_pitch_random"] = round_num(speaker.b4a_pitch_random, 3)
    spk_data["b4a_fade_in"] = round_num(speaker.b4a_fade_in, 3)
    spk_data["b4a_fade_out"] = round_num(speaker.b4a_fade_out, 3)
    spk_data["b4a_loop"] = speaker.b4a_loop
    spk_data["b4a_loop_count"] = speaker.b4a_loop_count
    spk_data["b4a_loop_count_random"] = speaker.b4a_loop_count_random
    spk_data["b4a_playlist_id"] = speaker.b4a_playlist_id

    _export_data["speakers"].append(spk_data)
    _export_uuid_cache[spk_data["uuid"]] = spk_data
    _bpy_uuid_cache[spk_data["uuid"]] = speaker

def process_sound(sound):
    if "export_done" in sound and sound["export_done"]:
        return
    sound["export_done"] = True

    sound_data = OrderedDict()
    sound_data["name"] = sound.name
    sound_data["uuid"] = gen_uuid(sound)

    if sound.packed_file is not None:
        packed_data = b4a_bin.get_packed_data(sound.packed_file.as_pointer())
        packed_file_path = get_json_relative_filepath(
                packed_resource_get_unique_name(sound))
        _packed_files_data[packed_file_path] = packed_data
        sound_data["filepath"] = packed_file_path
    else:
        sound_data["filepath"] = get_filepath(sound)

    _export_data["sounds"].append(sound_data)
    _export_uuid_cache[sound_data["uuid"]] = sound_data
    _bpy_uuid_cache[sound_data["uuid"]] = sound

def process_particle(particle):
    """type ParticleSettings"""

    if "export_done" in particle and particle["export_done"]:
        return
    particle["export_done"] = True

    part_data = OrderedDict()

    part_data["name"] = particle.name
    part_data["uuid"] = gen_uuid(particle)

    part_data["type"] = particle.type

    # emission
    part_data["count"] = particle.count
    part_data["emit_from"] = particle.emit_from

    part_data["frame_start"] = round_num(particle.frame_start, 3)
    part_data["frame_end"] = round_num(particle.frame_end, 3)
    part_data["lifetime"] = round_num(particle.lifetime, 3)
    part_data["lifetime_random"] = round_num(particle.lifetime_random, 3)

    # velocity
    part_data["normal_factor"] = round_num(particle.normal_factor, 3)
    part_data["factor_random"] = round_num(particle.factor_random, 3)

    # rotation

    # 'NONE, 'RAND', 'VELOCITY'
    part_data["angular_velocity_mode"] = particle.angular_velocity_mode
    part_data["angular_velocity_factor"] \
            = round_num(particle.angular_velocity_factor, 3)

    # physics
    part_data["particle_size"] = round_num(particle.particle_size, 3)
    part_data["mass"] = round_num(particle.mass, 3)
    # not used so far
    part_data["brownian_factor"] = round_num(particle.brownian_factor, 3)

    # renderer
    part_data["material"] = particle.material
    part_data["use_render_emitter"] = particle.use_render_emitter
    part_data["render_type"] = particle.render_type
    part_data["use_whole_group"] = particle.use_whole_group

    # field weights
    dct = part_data["effector_weights"] = OrderedDict()
    dct["gravity"] = round_num(particle.effector_weights.gravity, 3)
    dct["wind"] = round_num(particle.effector_weights.wind, 3)

    # "EMITTER"
    part_data["b4a_cyclic"] = particle.b4a_cyclic
    part_data["b4a_fade_in"] = round_num(particle.b4a_fade_in, 3)
    part_data["b4a_fade_out"] = round_num(particle.b4a_fade_out, 3)
    part_data["b4a_randomize_emission"] = particle.b4a_randomize_emission
    part_data["b4a_allow_nla"] = particle.b4a_allow_nla

    # "HAIR"
    part_data["b4a_initial_rand_rotation"] = particle.b4a_initial_rand_rotation
    part_data["b4a_rotation_type"] = particle.b4a_rotation_type
    part_data["b4a_rand_rotation_strength"] \
            = round_num(particle.b4a_rand_rotation_strength, 3)
    part_data["b4a_hair_billboard"] = particle.b4a_hair_billboard
    part_data["b4a_hair_billboard_type"] = particle.b4a_hair_billboard_type
    part_data["b4a_hair_billboard_jitter_amp"] \
            = round_num(particle.b4a_hair_billboard_jitter_amp, 3)
    part_data["b4a_hair_billboard_jitter_freq"] \
            = round_num(particle.b4a_hair_billboard_jitter_freq, 3)
    part_data["b4a_hair_billboard_geometry"] \
            = particle.b4a_hair_billboard_geometry
    part_data["b4a_dynamic_grass"] = particle.b4a_dynamic_grass
    part_data["b4a_dynamic_grass_scale_threshold"] \
            = particle.b4a_dynamic_grass_scale_threshold
    part_data["b4a_wind_bend_inheritance"] = particle.b4a_wind_bend_inheritance
    part_data["b4a_shadow_inheritance"] = particle.b4a_shadow_inheritance
    part_data["b4a_reflection_inheritance"] \
            = particle.b4a_reflection_inheritance
    part_data["b4a_vcol_from_name"] = particle.b4a_vcol_from_name
    part_data["b4a_vcol_to_name"] = particle.b4a_vcol_to_name

    bb_align_blender = particle.b4a_billboard_align
    if bb_align_blender == "XY":
        bb_align = "ZX"
    elif bb_align_blender == "ZX":
        bb_align = "XY"
    else:
        bb_align = bb_align_blender

    part_data["b4a_billboard_align"] = bb_align

    part_data["b4a_coordinate_system"] = particle.b4a_coordinate_system

    # process particle links
    # NOTE: it seams only single slot supported
    part_data["texture_slots"] = []
    for slot in particle.texture_slots:
        if slot and do_export(slot.texture):
            try:
                if not slot.texture:
                    raise MaterialError("No texture for the \"" + particle.name + "\" particle settings texture slot.")
                slot_data = OrderedDict()
                slot_data["use_map_size"] = slot.use_map_size
                slot_data["texture"] = gen_uuid_obj(slot.texture)
                process_texture(slot.texture)
                part_data["texture_slots"].append(slot_data)
            except MaterialError as ex:
                err(str(ex))

    if particle.render_type == "OBJECT":
        if particle.dupli_object is None:
            raise ExportError("Particle system error", particle, \
                    "Dupli object isn't specified")

        if not particle_object_is_valid(particle.dupli_object):
            raise ExportError("Particle system error", particle, \
                    "Wrong dupli object type '" + particle.dupli_object.type)

        if not do_export(particle.dupli_object):
            raise ExportError("Particle system error", particle, \
                    "Dupli object " + particle.dupli_object.name \
                    + " doesn't export")

        part_data["dupli_object"] = gen_uuid_obj(particle.dupli_object)
        process_object(particle.dupli_object)
    else:
        part_data["dupli_object"] = None

    part_data["dupli_weights"] = []
    if particle.render_type == "GROUP":

        if particle.dupli_group is None:
            raise ExportError("Particle system error", particle, \
                    "Dupli group isn't specified")

        part_data["dupli_group"] = gen_uuid_obj(particle.dupli_group)
        process_group(particle.dupli_group, for_particles=True)

        dg_uuid = part_data["dupli_group"]["uuid"]
        dg_data = _export_uuid_cache[dg_uuid]
        if not dg_data["objects"]:
            raise ExportError("Particle system error", particle,
                    "The \"" + dg_data["name"] + "\" dupli group contains no " +
                    "valid object for export");

        part_data["use_group_pick_random"] = particle.use_group_pick_random
        use_group_count = particle.use_group_count;
        part_data["use_group_count"] = use_group_count
        if use_group_count:
            process_particle_dupli_weights(part_data["dupli_weights"], particle)
    else:
        part_data["dupli_group"] = None
        part_data["use_group_pick_random"] = None
        part_data["use_group_count"] = None

    _export_data["particles"].append(part_data)
    _export_uuid_cache[part_data["uuid"]] = part_data
    _bpy_uuid_cache[part_data["uuid"]] = particle

def process_world(world):
    if "export_done" in world and world["export_done"]:
        return
    world["export_done"] = True

    world_data = OrderedDict()

    world_data["name"] = world.name
    world_data["uuid"] = gen_uuid(world)

    process_world_texture_slots(world_data, world)

    world_data["horizon_color"] = round_iterable(world.horizon_color, 4)
    world_data["zenith_color"] = round_iterable(world.zenith_color, 4)

    process_world_light_settings(world_data, world)
    process_world_shadow_settings(world_data, world)
    process_world_god_rays_settings(world_data, world)
    process_world_ssao_settings(world_data, world)
    process_world_color_correction_settings(world_data, world)
    process_world_sky_settings(world_data, world)
    process_world_bloom_settings(world_data, world)
    process_world_motion_blur_settings(world_data, world)

    world_data["b4a_glow_color"] = round_iterable(world.b4a_glow_color, 4)
    world_data["b4a_glow_factor"] = round_num(world.b4a_glow_factor, 2)
    world_data["b4a_fog_color"] = round_iterable(world.b4a_fog_color, 4)
    world_data["b4a_fog_density"] = round_num(world.b4a_fog_density, 4)

    _export_data["worlds"].append(world_data)
    _export_uuid_cache[world_data["uuid"]] = world_data
    _bpy_uuid_cache[world_data["uuid"]] = world

def process_world_light_settings(world_data, world):
    light_settings = world.light_settings

    dct = world_data["light_settings"] = OrderedDict()
    dct["use_environment_light"] = light_settings.use_environment_light
    dct["environment_energy"] = round_num(light_settings.environment_energy, 3)
    dct["environment_color"] = light_settings.environment_color

def process_world_shadow_settings(world_data, world):
    shadow = world.b4a_shadow_settings

    dct = world_data["b4a_shadow_settings"] = OrderedDict()

    dct["csm_resolution"] = int(shadow.csm_resolution)
    dct["self_shadow_polygon_offset"] = round_num(shadow.self_shadow_polygon_offset, 2)
    dct["self_shadow_normal_offset"] = round_num(shadow.self_shadow_normal_offset, 3)

    dct["b4a_enable_csm"] = shadow.b4a_enable_csm
    dct["csm_num"] = shadow.csm_num
    dct["csm_first_cascade_border"] = round_num(shadow.csm_first_cascade_border, 2)
    dct["first_cascade_blur_radius"] = round_num(shadow.first_cascade_blur_radius, 2)
    dct["csm_last_cascade_border"] = round_num(shadow.csm_last_cascade_border, 2)
    dct["last_cascade_blur_radius"] = round_num(shadow.last_cascade_blur_radius, 2)
    dct["fade_last_cascade"] = shadow.fade_last_cascade
    dct["blend_between_cascades"] = shadow.blend_between_cascades

def process_world_god_rays_settings(world_data, world):
    god_rays = world.b4a_god_rays_settings

    dct = world_data["b4a_god_rays_settings"] = OrderedDict()
    dct["intensity"] = round_num(god_rays.intensity, 2)
    dct["max_ray_length"] = round_num(god_rays.max_ray_length, 2)
    dct["steps_per_pass"] = round_num(god_rays.steps_per_pass, 1)


def process_world_ssao_settings(world_data, world):
    ssao = world.b4a_ssao_settings

    dct = world_data["b4a_ssao_settings"] = OrderedDict()
    dct["radius_increase"] = round_num(ssao.radius_increase, 2)
    dct["hemisphere"] = ssao.hemisphere
    dct["blur_depth"] = ssao.blur_depth
    dct["blur_discard_value"] = round_num(ssao.blur_discard_value, 2)
    dct["influence"] = round_num(ssao.influence, 3)
    dct["dist_factor"] = round_num(ssao.dist_factor, 2)
    dct["samples"] = int(ssao.samples)

def process_world_color_correction_settings(world_data, world):
    ccs = world.b4a_color_correction_settings

    dct = world_data["b4a_color_correction_settings"] = OrderedDict()
    dct["brightness"] = round_num(ccs.brightness, 2)
    dct["contrast"] = round_num(ccs.contrast, 2)
    dct["exposure"] = round_num(ccs.exposure, 2)
    dct["saturation"] = round_num(ccs.saturation, 2)

def process_world_sky_settings(world_data, world):
    sky = world.b4a_sky_settings

    dct = world_data["b4a_sky_settings"] = OrderedDict()
    dct["reflexible"] = sky.reflexible
    dct["reflexible_only"] = sky.reflexible_only
    dct["procedural_skydome"] = sky.procedural_skydome
    dct["use_as_environment_lighting"] = sky.use_as_environment_lighting
    dct["color"] = round_iterable(sky.color, 4)
    dct["rayleigh_brightness"] = round_num(sky.rayleigh_brightness, 2)
    dct["mie_brightness"] = round_num(sky.mie_brightness, 2)
    dct["spot_brightness"] = round_num(sky.spot_brightness, 1)
    dct["scatter_strength"] = round_num(sky.scatter_strength, 2)
    dct["rayleigh_strength"] = round_num(sky.rayleigh_strength, 2)
    dct["mie_strength"] = round_num(sky.mie_strength, 4)
    dct["rayleigh_collection_power"] = round_num(sky.rayleigh_collection_power, 2)
    dct["mie_collection_power"] = round_num(sky.mie_collection_power, 2)
    dct["mie_distribution"] = round_num(sky.mie_distribution, 2)

def process_world_bloom_settings(world_data, world):
    bloom = world.b4a_bloom_settings

    dct = world_data["b4a_bloom_settings"] = OrderedDict()
    dct["key"] = round_num(bloom.key, 2)
    dct["blur"] = round_num(bloom.blur, 2)
    dct["edge_lum"] = round_num(bloom.edge_lum, 2)

def process_world_motion_blur_settings(world_data, world):
    motion_blur = world.b4a_motion_blur_settings

    dct = world_data["b4a_motion_blur_settings"] = OrderedDict()
    dct["motion_blur_factor"] = round_num(motion_blur.motion_blur_factor, 3)
    dct["motion_blur_decay_threshold"] \
            = round_num(motion_blur.motion_blur_decay_threshold, 3)

def matrix4x4_to_list(m):
    m = m.transposed()
    result = []
    for i in range(0, 4):
        v = m[i]
        for j in range(0, 4):
            result.append(v[j])
    return result

def process_animation_data(obj_data, component, actions):
    adata = component.animation_data
    if adata:
        dct = obj_data["animation_data"] = OrderedDict()

        action = select_action(adata.action, actions)
        if action and do_export(action):
            dct["action"] = gen_uuid_obj(action)
        else:
            dct["action"] = None

        dct["nla_tracks"] = []
        if adata.nla_tracks:
            process_object_nla(dct["nla_tracks"], adata.nla_tracks, actions)
    else:
        obj_data["animation_data"] = None

def is_identity_matrix(m):
    identity = mathutils.Matrix()
    if m == identity:
        return True
    else:
        return False

def find_modifier(obj, mtype):
    for modifier in obj.modifiers:
        if modifier.type == mtype:
            return modifier

    return None

def process_object_modifiers(mod_data, modifiers):
    for modifier in modifiers:
        modifier_data = OrderedDict()
        modifier_data["name"] = modifier.name

        # NOTE: don't export modifier in some cases
        if not process_modifier(modifier_data, modifier):
            continue
        modifier_data["type"] = modifier.type
        mod_data.append(modifier_data)

def process_modifier(modifier_data, mod):
    if mod.type == "ARMATURE":
        if mod.object and do_export(mod.object):
            modifier_data["object"] = gen_uuid_obj(mod.object)
            process_object(mod.object)
        else:
            err("The \"" + mod.name
                    + "\" armature modifier has no armature object or it is not exported. "
                        + "Modifier removed.")
            return False
    elif mod.type == "ARRAY":
        modifier_data["fit_type"] = mod.fit_type

        # 3 values for each fit type
        modifier_data["count"] = mod.count
        modifier_data["fit_length"] = round_num(mod.fit_length, 3)
        if mod.curve and object_is_valid(mod.curve):
            modifier_data["curve"] = gen_uuid_obj(mod.curve)
            process_object(mod.curve)
        else:
            modifier_data["curve"] = None

        modifier_data["use_constant_offset"] = mod.use_constant_offset

        cod = mod.constant_offset_displace
        cod = round_iterable([cod[0], cod[2], -cod[1]], 5)
        modifier_data["constant_offset_displace"] = cod

        modifier_data["use_relative_offset"] = mod.use_relative_offset

        rod = mod.relative_offset_displace
        rod = round_iterable([rod[0], rod[2], -rod[1]], 5)
        modifier_data["relative_offset_displace"] = rod

        modifier_data["use_object_offset"] = mod.use_object_offset
        if mod.offset_object and object_is_valid(mod.offset_object):
            modifier_data["offset_object"] = gen_uuid_obj(mod.offset_object)
            process_object(mod.offset_object)
        else:
            modifier_data["offset_object"] = None

    elif mod.type == "CURVE":
        if mod.object and object_is_valid(mod.object):
            modifier_data["object"] = gen_uuid_obj(mod.object)
            process_object(mod.object)
            if not (len(mod.object.data.splines) \
                    and mod.object.data.splines[0].type == "NURBS" \
                    and mod.object.data.splines[0].use_endpoint_u):
                err("The \"" + mod.name
                        + "\" curve modifier has unsupported curve object \""
                        + mod.object.name + "\". Modifier removed.")
                return False
        else:
            err("The \"" + mod.name
                    + "\"curve modifier has no curve object. Modifier removed.")
            return False
        modifier_data["deform_axis"] = mod.deform_axis

    return True

def process_object_constraints(constraints):
    """export constraints (target attribute can have link to other objects)"""

    constraints_data = []
    for cons in constraints:
        cons_data = OrderedDict()
        cons_data["name"] = cons.name

        process_object_constraint(cons_data, cons)
        cons_data["mute"] = cons.mute
        cons_data["type"] = cons.type

        constraints_data.append(cons_data)

    return constraints_data

def process_object_constraint(cons_data, cons):
    if (cons.type == "COPY_LOCATION" or cons.type == "COPY_ROTATION" or
            cons.type == "COPY_SCALE" or cons.type == "COPY_TRANSFORMS"):

        cons_data["target"] = obj_cons_target(cons)
        cons_data["subtarget"] = cons.subtarget
        cons_data["use_x"] = cons.use_x
        # z <-> y
        cons_data["use_y"] = cons.use_z
        cons_data["use_z"] = cons.use_y

    elif cons.type == "LOCKED_TRACK" and cons.name == "REFLECTION PLANE":
        cons_data["target"] = obj_cons_target(cons)

    elif cons.type == "SHRINKWRAP":
        cons_data["target"] = obj_cons_target(cons)
        cons_data["shrinkwrap_type"] = cons.shrinkwrap_type
        cons_data["use_x"] = cons.use_x
        # z <-> y
        cons_data["use_y"] = cons.use_z
        cons_data["use_z"] = cons.use_y
        cons_data["influence"] = round_num(cons.influence, 3)
        cons_data["distance"] = round_num(cons.distance, 3)

    elif cons.type == "RIGID_BODY_JOINT":
        cons_data["target"] = obj_cons_target(cons)

        cons_data["pivot_type"] = cons.pivot_type

        # z -> y; y -> -z
        cons_data["pivot_x"] = round_num(cons.pivot_x, 3)
        cons_data["pivot_y"] = round_num(cons.pivot_z, 3)
        cons_data["pivot_z"] = round_num(-cons.pivot_y, 3)

        cons_data["axis_x"] = round_num(cons.axis_x, 4)
        cons_data["axis_y"] = round_num(cons.axis_z, 4)
        cons_data["axis_z"] = round_num(-cons.axis_y, 4)

        # limits
        cons_data["use_limit_x"] = cons.use_limit_x
        cons_data["use_limit_y"] = cons.use_limit_z
        cons_data["use_limit_z"] = cons.use_limit_y

        cons_data["use_angular_limit_x"] = cons.use_angular_limit_x
        cons_data["use_angular_limit_y"] = cons.use_angular_limit_z
        cons_data["use_angular_limit_z"] = cons.use_angular_limit_y

        # z -> y; y -> -z; min y -> max z; max y -> min z
        cons_data["limit_max_x"] = round_num(cons.limit_max_x, 3)
        cons_data["limit_max_y"] = round_num(cons.limit_max_z, 3)
        cons_data["limit_max_z"] = round_num(-cons.limit_min_y, 3)
        cons_data["limit_min_x"] = round_num(cons.limit_min_x, 3)
        cons_data["limit_min_y"] = round_num(cons.limit_min_z, 3)
        cons_data["limit_min_z"] = round_num(-cons.limit_max_y, 3)

        cons_data["limit_angle_max_x"] = round_num(cons.limit_angle_max_x, 4)
        cons_data["limit_angle_max_y"] = round_num(cons.limit_angle_max_z, 4)
        cons_data["limit_angle_max_z"] = round_num(-cons.limit_angle_min_y, 4)
        cons_data["limit_angle_min_x"] = round_num(cons.limit_angle_min_x, 4)
        cons_data["limit_angle_min_y"] = round_num(cons.limit_angle_min_z, 4)
        cons_data["limit_angle_min_z"] = round_num(-cons.limit_angle_max_y, 4)

def process_object_lod_levels(obj):
    """export lods"""

    if obj.type != "MESH":
        return []

    lod_levels = obj.lod_levels
    obj_name = obj.name
    cons = obj.constraints
    lods_num = round_num(len(obj.b4a_lods), 1)
    lod_dist = round_num(obj.b4a_lod_distance, 2)

    lod_levels_data = []
    is_target_lod_empty = True

    if not len(lod_levels) and lod_dist < 10000:
        for con in cons:
            if con.type == "LOCKED_TRACK" and con.target:
                lods_data = OrderedDict()
                lods_data["distance"] = lod_dist

                if con.target:
                    process_object(con.target)
                    lods_data["object"] = gen_uuid_obj(con.target)
                else:
                    lods_data["object"] = None

                lods_data["use_mesh"] = True
                lods_data["use_material"] = True
                lod_dist = round_num(con.target.b4a_lod_distance, 2)
                lod_levels_data.append(lods_data)

        if lod_dist < 10000:
            lods_data = OrderedDict()
            lods_data["distance"] = lod_dist
            lods_data["object"] = None
            lods_data["use_mesh"] = True
            lods_data["use_material"] = True
            lod_levels_data.append(lods_data)

    for lod in lod_levels:

        if not is_target_lod_empty:
            err("Ignoring LODs after empty LOD for the \"" + obj_name + "\" object.")
            break

        if obj == lod.object:
            continue

        lods_data = OrderedDict()
        lods_data["distance"] = lod.distance

        if lod.object:
            process_object(lod.object)
            lods_data["object"] = gen_uuid_obj(lod.object)
        else:
            lods_data["object"] = None

        lods_data["use_mesh"] = lod.use_mesh
        lods_data["use_material"] = lod.use_material

        lod_levels_data.append(lods_data)

        if not lod.object:
            is_target_lod_empty = False

    return lod_levels_data

def obj_cons_target(cons):
    if not cons.target:
        raise ExportError("Object constraint has no target", cons)

    if cons.target and object_is_valid(cons.target):
        target_uuid = gen_uuid_obj(cons.target)
        process_object(cons.target)
    else:
        target_uuid = None

    return target_uuid

def process_object_particle_systems(obj):
    psystems_data = []

    if obj.particle_systems:
        for psys in obj.particle_systems:
            if do_export(psys.settings):

                psys_data = OrderedDict()

                psys_data["name"] = psys.name
                psys_data["seed"] = psys.seed

                # export particle transforms for hairs
                # [x0,y0,z0,scale0,x1...]
                if (psys.settings.type == "HAIR" and not
                        psys.settings.b4a_randomize_location):
                    transforms_length = len(psys.particles) * 4
                    ptrans_ptr = b4a_bin.create_buffer_float(transforms_length)

                    for i in range(len(psys.particles)):
                        particle = psys.particles[i]
                        x,y,z = particle.hair_keys[0].co.xyz

                        # calc length as z coord of the last hair key
                        #length = particle.hair_keys[-1:][0].co_local.z
                        #length = particle.hair_keys[-1:][0].co.z - z
                        length = (particle.hair_keys[-1:][0].co_local.xyz -
                                    particle.hair_keys[0].co_local.xyz).length
                        scale = particle.size * length

                        # translate coords: x,z,-y
                        ptrans_ptr = b4a_bin.buffer_insert_float(ptrans_ptr, i * 4, x)
                        ptrans_ptr = b4a_bin.buffer_insert_float(ptrans_ptr, i * 4 + 1, z)
                        ptrans_ptr = b4a_bin.buffer_insert_float(ptrans_ptr, i * 4 + 2, -y)
                        ptrans_ptr = b4a_bin.buffer_insert_float(ptrans_ptr, i * 4 + 3, scale)

                    ptrans = b4a_bin.get_buffer_float(ptrans_ptr, transforms_length)

                    psys_data["transforms"] = [
                        len(_bpy_bindata_float) // BINARY_FLOAT_SIZE,
                        len(ptrans) // BINARY_FLOAT_SIZE
                    ]
                    _bpy_bindata_float.extend(ptrans)
                else:
                    psys_data["transforms"] = [0, 0]

                psys_data["settings"] = gen_uuid_obj(psys.settings)
                process_particle(psys.settings)

                psystems_data.append(psys_data)

    return psystems_data

def process_node_tree(data, tree_source):
    node_tree = tree_source.node_tree

    if node_tree == None:
        data["node_tree"] = None
        return

    dct = data["node_tree"] = OrderedDict()
    dct["nodes"] = []

    has_normalmap_node = False
    has_material_node  = False

    # node tree nodes
    for node in node_tree.nodes:
        if not validate_node(node):
            raise MaterialError("The " + "\"" + node.name + "\"" +" node is not supported. "
                    + "Nodes will be disable for \"" + tree_source.name + "\".")

        node_data = OrderedDict()

        node_data["name"] = node.name
        node_data["type"] = node.type

        process_node_sockets(node_data, "inputs", node.inputs)
        process_node_sockets(node_data, "outputs", node.outputs)

        if node.type == "GEOMETRY":
            if check_uv_layers_limited(_curr_mesh, node.uv_layer):
                node_data["uv_layer"] = node.uv_layer
            else:
                node_data["uv_layer"] = ""
                raise MaterialError("Exported UV-layer is missing in node \"GEOMETRY\".")

            if check_vertex_color(_curr_mesh, node.color_layer):
                node_data["color_layer"] = node.color_layer
            else:
                node_data["color_layer"] = ""

        if node.type == "GROUP":
            if node.node_tree.name == "REFRACTION":
                if bpy.data.scenes[0].b4a_render_refractions:
                    curr_alpha_blend = _curr_material_stack[-1].game_settings.alpha_blend
                    if curr_alpha_blend == "OPAQUE" or curr_alpha_blend == "CLIP":
                        raise MaterialError("Using \"REFRACTION\" node with incorrect type of Alpha Blend.")

            node_data["node_tree_name"] = node.node_tree.name
            node_data["node_group"] = gen_uuid_obj(node.node_tree)
            process_node_group(node)

        elif node.type == "MAPPING":
            node_data["translation"] = round_iterable(node.translation, 3)
            node_data["rotation"] = round_iterable(node.rotation, 3)
            node_data["scale"] = round_iterable(node.scale, 3)

            node_data["use_min"] = node.use_min
            node_data["use_max"] = node.use_max

            node_data["min"] = round_iterable(node.min, 3)
            node_data["max"] = round_iterable(node.max, 3)

        elif node.type == "MATERIAL" or node.type == "MATERIAL_EXT":
            if node.material:
                node_data["material"] = gen_uuid_obj(node.material)
                if not("in_use" in node.material and node.material["in_use"] == True):
                    process_material(node.material)
            else:
                node_data["material"] = None

            node_data["use_diffuse"] = node.use_diffuse
            node_data["use_specular"] = node.use_specular
            node_data["invert_normal"] = node.invert_normal

            has_material_node = True

        elif node.type == "MATH":
            node_data["operation"] = node.operation

        elif node.type == "MIX_RGB":
            node_data["blend_type"] = node.blend_type

        elif node.type == "TEXTURE":

            for output in node.outputs:
                if output.identifier == "Normal" and output.is_linked:
                    has_normalmap_node = True
                    break

            if node.texture and do_export(node.texture):
                node_data["texture"] = gen_uuid_obj(node.texture)
                process_texture(node.texture)
            else:
                node_data["texture"] = None

        elif node.type == "VECT_MATH":
            node_data["operation"] = node.operation

        elif node.type == "LAMP":
            if node.lamp_object is None:
                # err("\"" + node.name + "\"" + " has no lamp object.")
                raise MaterialError("The \"" + node.name + "\" LAMP node has no lamp object.")
            else:
                node_data["lamp"] = gen_uuid_obj(node.lamp_object)

        dct["nodes"].append(node_data)

    if has_normalmap_node and not has_material_node:
        raise ExportError("The material has a normal map but doesn't have " +
                "any material nodes", tree_source)

    # node tree links
    dct["links"] = []
    for link in node_tree.links:
        link_data = OrderedDict()

        # name is unique identifier here
        link_data["from_node"] = OrderedDict({ "name": link.from_node.name })
        link_data["to_node"] = OrderedDict({ "name": link.to_node.name })

        # identifier is unique identifier here
        link_data["from_socket"] \
                = OrderedDict({ "identifier": link.from_socket.identifier })
        link_data["to_socket"] \
                = OrderedDict({ "identifier": link.to_socket.identifier })

        dct["links"].append(link_data)

    # node animation data
    process_animation_data(dct, node_tree, bpy.data.actions)

def validate_node(node):
    if node.bl_idname == "ShaderNodeGroup":

        if not node.node_tree:
            return False

        for group_node in node.node_tree.nodes:
            if not validate_node(group_node):
                print("Not valid: ", group_node.bl_idname)
                return False
        return True
    else:
        return node.bl_idname in SUPPORTED_NODES

def process_node_sockets(node_data, type_str, sockets):
    node_data[type_str] = []

    if len(sockets):
        for sock in sockets:
            # system socket has no name
            if not sock.name:
                continue
            sock_data = OrderedDict()
            sock_data["name"] = sock.name
            sock_data["identifier"] = sock.identifier
            sock_data["is_linked"] = sock.is_linked

            rna_ident = sock.rna_type.identifier
            if (rna_ident == "NodeSocketVector" or rna_ident == "NodeSocketColor" or
                    rna_ident == "NodeSocketVectorDirection"):
                sock_data["default_value"] = round_iterable(sock.default_value, 3)
            else:
                sock_data["default_value"] = round_num(sock.default_value, 3)

            node_data[type_str].append(sock_data)

def process_node_group(node_group):
    if "export_done" in node_group.node_tree and node_group.node_tree["export_done"]:
        return
    node_group.node_tree["export_done"] = True
    ng_data = OrderedDict()
    ng_data["name"] = node_group.node_tree.name
    ng_data["uuid"] = gen_uuid(node_group.node_tree)
    process_node_tree(ng_data, node_group)

    _export_data["node_groups"].append(ng_data)
    _export_uuid_cache[ng_data["uuid"]] = ng_data
    _bpy_uuid_cache[ng_data["uuid"]] = node_group

def process_world_texture_slots(world_data, world):
    slots = world.texture_slots
    world_data["texture_slots"] = []
    for i in range(len(slots)):
        slot = slots[i]
        if slot:
            try:
                if not slot.texture:
                    raise MaterialError("No texture in the \"" + world.name + "\" world texture slot.")
                if do_export(slot.texture):
                    if slot.texture.b4a_use_sky != "OFF" and len(slot.texture.users_material) == 0:
                        slot_data = OrderedDict()
                        check_tex_slot(slot)

                        # there are a lot of properties in addition to these
                        slot_data["texture_coords"] = slot.texture_coords
                        slot_data["use_rgb_to_intensity"] = slot.use_rgb_to_intensity
                        slot_data["use_stencil"] = slot.use_stencil
                        slot_data["offset"] = round_iterable(slot.offset, 3)
                        slot_data["scale"] = round_iterable(slot.scale, 3)
                        slot_data["blend_type"] = slot.blend_type
                        if slot.texture.type != "ENVIRONMENT_MAP":
                            raise ExportError(slot.texture.type + " isn't supported", world)
                        slot_data["texture"] = gen_uuid_obj(slot.texture)

                        process_texture(slot.texture)
                        world_data["texture_slots"].append(slot_data)
            except MaterialError as ex:
                err(str(ex))

def process_material_texture_slots(mat_data, material):
    global _curr_mesh
    slots = material.texture_slots
    use_slots = material.use_textures

    mat_data["texture_slots"] = []
    for i in range(len(slots)):
        slot = slots[i]
        use = use_slots[i]
        if slot and use:
            # check texture availability
            if not slot.texture:
                raise MaterialError("No texture in the texture slot.")
            if do_export(slot.texture):
                if slot.use_map_color_diffuse and slot.texture.type == "ENVIRONMENT_MAP":
                    raise ExportError("Use of ENVIRONMENT_MAP as diffuse " \
                        "color is not supported", material)

                slot_data = OrderedDict()

                tc = slot.texture_coords
                check_tex_slot(slot)

                slot_data["texture_coords"] = tc

                if tc == "UV":
                    if check_uv_layers_limited(_curr_mesh, slot.uv_layer):
                        slot_data["uv_layer"] = slot.uv_layer
                    else:
                        slot_data["uv_layer"] = ""
                        raise MaterialError("Exported UV-layer is missing in texture \"" + slot.texture.name + "\".")
                else:
                    slot_data["uv_layer"] = ""

                slot_data["use_map_color_diffuse"] = slot.use_map_color_diffuse
                slot_data["diffuse_color_factor"] \
                        = round_num(slot.diffuse_color_factor, 3)
                slot_data["use_map_alpha"] = slot.use_map_alpha
                slot_data["alpha_factor"] = round_num(slot.alpha_factor, 3)
                slot_data["use_map_color_spec"] = slot.use_map_color_spec
                slot_data["specular_color_factor"] \
                        = round_num(slot.specular_color_factor, 3)
                slot_data["use_map_normal"] = slot.use_map_normal
                slot_data["normal_factor"] = round_num(slot.normal_factor, 3)
                slot_data["use_map_mirror"] = slot.use_map_mirror
                slot_data["mirror_factor"] = round_num(slot.mirror_factor, 3)
                slot_data["use_rgb_to_intensity"] = slot.use_rgb_to_intensity
                slot_data["use_stencil"] = slot.use_stencil
                slot_data["offset"] = round_iterable(slot.offset, 3)
                slot_data["scale"] = round_iterable(slot.scale, 3)
                slot_data["blend_type"] = slot.blend_type
                slot_data["texture"] = gen_uuid_obj(slot.texture)
                process_texture(slot.texture)

                mat_data["texture_slots"].append(slot_data)

def process_particle_dupli_weights(dupli_weights_data, particle):
    for i in range(len(particle.dupli_weights)):
        obj = particle.dupli_group.objects[i]

        if do_export(obj) and object_is_valid(obj):
            weight = particle.dupli_weights[i]
            # when exporting via command line linked particles are exported wrong
            if weight.name == "No object":
                raise InternalError("Missing particles dupli weights in particle system " \
                        + particle.name)

            weight_data = OrderedDict()
            weight_data["name"] = weight.name
            weight_data["count"] = weight.count
            dupli_weights_data.append(weight_data)

def round_num(n, level=0):
    #NOTE: clamping to protect from possible Infinity values
    n = max(-(2**31),min(n, 2**31 - 1))
    rounded = round(n, level)
    if rounded%1 == 0:
        rounded = math.trunc(rounded)
    return rounded

def round_iterable(num_list, level=0):
    return [round_num(item, level) for item in num_list]

def get_default_path():
    scene = bpy.data.scenes[0]
    if not (scene.b4a_export_path_json == ""):
        return bpy.path.abspath(scene.b4a_export_path_json)

    # if it was already exported reuse that path e.g. from objects
    objects = bpy.data.objects
    for obj in objects:
        if not obj.library and not obj.proxy and do_export(obj): # we want only objects in main
            path = get_component_export_path(obj)
            if len(path) > 0:
                return bpy.path.abspath(path)

    blend_path = os.path.splitext(bpy.data.filepath)[0]
    if len(blend_path) > 0:
        return blend_path + ".json"
    else:
        return "untitled.json"

def set_default_path(path):
    if bpy.data.filepath != "":
        try:
            path = bpy.path.relpath(path)
        except ValueError as exp:
            _file_error = exp
            raise FileError("Export to different disk is forbidden")
    for i in range(len(bpy.data.scenes)):
        bpy.data.scenes[i].b4a_export_path_json = guard_slashes(path)

class B4A_ExportProcessor(bpy.types.Operator):
    """Export for Blend4Avango (.json)"""
    bl_idname = "export_scene.b4a_json"
    bl_label = "b4a Export"

    filepath = bpy.props.StringProperty(subtype='FILE_PATH', default = "")

    do_autosave = bpy.props.BoolProperty(
        name = "Autosave main file",
        description = "Proper linking between exported files requires saving file after exporting",
        default = True
    )

    override_filepath = bpy.props.StringProperty(
        name = "Filepath",
        description = "Required for running in command line",
        default = ""
    )

    save_export_path = bpy.props.BoolProperty(
        name = "Save export path",
        description = "Save export path in blend file",
        default = True
    )

    is_html_export = bpy.props.BoolProperty(
        name = "Is HTML export",
        description = "Is html export",
        default = False
    )

    def execute(self, context):
        if self.override_filepath:
            self.filepath = self.override_filepath

        # append .json if needed
        filepath_val = self.filepath
        if not filepath_val.lower().endswith(".json"):
            filepath_val += ".json"

        try:
            self.run(filepath_val)
            # uncomment to see profiler results
            #cProfile.runctx("self.run(filepath_val)", globals(), locals())
        except ExportError as error:
            global _export_error
            _export_error = error
            bpy.ops.b4a.export_error_dialog('INVOKE_DEFAULT')
            return {'CANCELLED'}
        except FileError as error:
            global _file_error
            _file_error = error
            bpy.ops.b4a.file_error_dialog('INVOKE_DEFAULT')
            return {'CANCELLED'}

        return {"FINISHED"}

    def invoke(self, context, event):
        self.filepath = get_default_path()
        wm = context.window_manager
        wm.fileselect_add(self)

        # NOTE: select all layers on all scenes to avoid issue with particle systems
        # NOTE: do it before execution!!!
        if bpy.data.particles:
            scenes_store_select_all_layers()
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        # NOTE: restore selected layers
        if bpy.data.particles:
            scenes_restore_selected_layers()

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "do_autosave")

    def run(self, export_filepath):
        global _bpy_bindata_int
        _bpy_bindata_int = bytearray();

        global _bpy_bindata_short
        _bpy_bindata_short = bytearray();

        global _bpy_bindata_ushort
        _bpy_bindata_ushort = bytearray();

        global _bpy_bindata_float
        _bpy_bindata_float = bytearray();

        global _export_filepath
        _export_filepath = export_filepath

        global _export_data
        _export_data = OrderedDict()

        global _main_json_str
        _main_json_str = ""

        global _curr_scene
        _curr_scene = None

        global _curr_material_stack
        _curr_material_stack = []

        global _fallback_camera
        _fallback_camera = None

        global _fallback_world
        _fallback_world = None

        global _fallback_material
        _fallback_material = None

        global _curr_mesh
        _curr_mesh = None

        global _export_uuid_cache
        _export_uuid_cache = {}

        global _bpy_uuid_cache
        _bpy_uuid_cache = {}

        global _overrided_meshes
        _overrided_meshes = []

        global _is_html_export
        _is_html_export = self.is_html_export

        global _b4a_export_warnings
        _b4a_export_warnings = []

        global _b4a_export_errors
        _b4a_export_errors = []

        global _vehicle_integrity
        _vehicle_integrity = {}

        global _packed_files_data
        _packed_files_data = {}

        global _file_write_error

        # escape from edit mode
        if bpy.context.mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")

        assign_do_not_export_flags()

        tags = [
            "actions",
            "images",
            "textures",
            "materials",
            "meshes",
            "armatures",
            "cameras",
            "curves",
            "lamps",
            "sounds",
            "speakers",
            "particles",
            "objects",
            "groups",
            "scenes",
            "worlds",
            "node_groups"
        ]

        # generate export data
        _export_data["b4a_format_version"] = blend4avango.bl_info["b4a_format_version"]
        _export_data["b4a_filepath_blend"] = get_filepath_blend(export_filepath)

        attach_export_properties(tags)
        process_components(tags)
        detach_export_properties(tags)

        check_vehicle_integrity()

        clean_exported_data()

        _export_data["binaries"] = []
        binary_data = OrderedDict()
        if len(_bpy_bindata_int) + len(_bpy_bindata_float) \
                 + len(_bpy_bindata_short) + len(_bpy_bindata_ushort):
            base = os.path.splitext(os.path.basename(export_filepath))[0]
            binary_load_path = base + '.bin'
            base = os.path.splitext(export_filepath)[0]
            binary_export_path = base + '.bin'
            binary_data["binfile"] = binary_load_path
        else:
            binary_export_path = None
            binary_data["binfile"] = None
        binary_data["int"] = 0
        binary_data["float"] = len(_bpy_bindata_int)
        binary_data["short"] = binary_data["float"] + len(_bpy_bindata_float)
        binary_data["ushort"] = binary_data["short"] + len(_bpy_bindata_short)
        _export_data["binaries"].append(binary_data)

        _export_data["b4a_export_warnings"] = _b4a_export_warnings
        _export_data["b4a_export_errors"] = _b4a_export_errors

        # NOTE: much faster than dumping immediately to file (json.dump())
        if JSON_PRETTY_PRINT:
            _main_json_str = json.dumps(_export_data, indent=2, separators=(',', ': '))
        else:
            _main_json_str = json.dumps(_export_data)

        if not _is_html_export:
            # write packed files (images, sounds) for non-html export
            for path in _packed_files_data:
                abs_path = os.path.join(os.path.dirname(export_filepath), path)
                try:
                    f = open(abs_path, "wb")
                except IOError as exp:
                    _file_error = exp
                    raise FileError("Permission denied")
                else:
                    f.write(_packed_files_data[path])
                    f.close()

            # write main binary and json files
            try:
                f  = open(export_filepath, "w")
                if binary_export_path is not None:
                    fb = open(binary_export_path, "wb")
            except IOError as exp:
                _file_error = exp
                raise FileError("Permission denied")
            else:
                f.write(_main_json_str)
                f.close()
                if self.save_export_path:
                    set_default_path(export_filepath)

                print("Scene saved to " + export_filepath)

                if binary_export_path is not None:
                    # NOTE: write data in this order (4-bit, 4-bit, 2-bit, 2-bit
                    # arrays) to simplify data loading
                    fb.write(_bpy_bindata_int)
                    fb.write(_bpy_bindata_float)
                    fb.write(_bpy_bindata_short)
                    fb.write(_bpy_bindata_ushort)
                    fb.close()
                    print("Binary data saved to " + binary_export_path)

                print("EXPORT OK")


        if self.do_autosave:
            filepath = bpy.data.filepath
            if filepath:
                if os.access(filepath, os.W_OK):
                    bpy.ops.wm.save_mainfile(filepath=filepath)
                    print("File autosaved to " + filepath)
                else:
                    print("Could not autosave: permission denied")
            else:
                print("Could not autosave: no file")

        return "exported"

def check_vehicle_integrity():
    for name in _vehicle_integrity:
        if _vehicle_integrity[name]["chassis"] is None \
                and _vehicle_integrity[name]["hull"] is None:
            ref_obj = None
            for prop in _vehicle_integrity[name]:
                if _vehicle_integrity[name][prop] is not None:
                    ref_obj = _vehicle_integrity[name][prop]
                    break
            if ref_obj is not None:
                raise ExportError("Incomplete vehicle", ref_obj,
                        "The \"" + name + "\" vehicle doesn't have any chassis or hull")
        elif _vehicle_integrity[name]["chassis"] is not None \
                and _vehicle_integrity[name]["wheel"] is None:
            raise ExportError("Incomplete vehicle",
                    _vehicle_integrity[name]["chassis"],
                    "The \"" + name + "\" vehicle requires at least one wheel")
        elif _vehicle_integrity[name]["hull"] is not None \
                and _vehicle_integrity[name]["bob"] is None:
            raise ExportError("Incomplete vehicle",
                    _vehicle_integrity[name]["hull"],
                    "The \"" + name + "\" vehicle requires at least one bob")

def check_shared_data(export_data):
    objects = export_data["objects"]
    meshes = export_data["meshes"]
    materials = export_data["materials"]

    # check compatibility of objects with shared mesh
    for mesh_data in meshes:
        mesh_users = []
        for obj_data in objects:
            if obj_data["type"] == "MESH":
                uuid = obj_data["data"]["uuid"]
                if mesh_data == _export_uuid_cache[uuid]:
                    mesh_users.append(obj_data)
        check_objs_shared_mesh_compat(mesh_users)

    # check compatibility of meshes with shared material
    for mat_data in materials:
        check_material_nodes_links(mat_data);

        mat_users = []
        for mesh_data in meshes:
            for mesh_mat in mesh_data["materials"]:
                if mat_data == _export_uuid_cache[mesh_mat["uuid"]]:
                    mat_users.append(mesh_data)

        check_meshes_shared_mat_compat(mat_users)

def check_objs_shared_mesh_compat(mesh_users):
    if len(mesh_users) <= 1:
        return

    # objects with shared mesh, non-empty groups and no to_mesh() incompatible
    for obj_data in mesh_users:
        mesh_uuid = obj_data["data"]["uuid"]
        mesh_data = _export_uuid_cache[mesh_uuid]

        obj = _bpy_uuid_cache[obj_data["uuid"]]
        if not obj_to_mesh_needed(obj) and mesh_data["vertex_groups"]:
            raise ExportError("Incompatible objects with a shared mesh", obj,
                    "The object " + obj_data["name"] +
                    " has both vertex groups and shared mesh")

def check_material_nodes_links(mat_data):
    if mat_data["node_tree"] is not None:
        vector_types = ["NodeSocketColor", "NodeSocketVector",
                "NodeSocketVectorDirection"]

        material = _bpy_uuid_cache[mat_data["uuid"]]
        links = material.node_tree.links
        for link in links:
            sock_from = link.from_socket.rna_type.identifier
            sock_to = link.to_socket.rna_type.identifier

            if (sock_from in vector_types) != (sock_to in vector_types):
                raise ExportError("Node material invalid", material,
                        "Check sockets compatibility: " + link.from_node.name \
                        + " to " + link.to_node.name)

def check_meshes_shared_mat_compat(mat_users):
    for i in range(len(mat_users) - 1):
        mesh_data = mat_users[i]
        mesh_data_next = mat_users[i + 1]
        mesh = _bpy_uuid_cache[mesh_data["uuid"]]
        mesh_next = _bpy_uuid_cache[mesh_data_next["uuid"]]

        if len(mesh_data["uv_textures"]) != len(mesh_data_next["uv_textures"]):
            raise ExportError("Incompatible meshes", mesh, "Check " \
                    + mesh_data["name"] + " and " + mesh_data_next["name"] \
                    + " UV Maps")

        if len(mesh.vertex_colors) != len(mesh_next.vertex_colors):
            raise ExportError("Incompatible meshes", mesh, "Check " \
                    + mesh_data["name"] + " and " + mesh_data_next["name"] \
                    + " Vertex colors")

def create_fallback_camera(scene_data):
    global _fallback_camera

    camera_data = bpy.data.cameras.new("FALLBACK_CAMERA")
    _fallback_camera = bpy.data.objects.new(name="FALLBACK_CAMERA",
            object_data=camera_data)
    view_3d_region = None
    screen_name = bpy.context.screen.name
    for area in bpy.data.screens[screen_name].areas:
        if area.type == "VIEW_3D":
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    view_3d_region = space.region_3d
                    break

    if view_3d_region is None:
        for area in bpy.data.screens[screen_name].areas:
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    view_3d_region = space.region_3d
                    break
            if view_3d_region is not None:
                break

    if view_3d_region is None:
        _fallback_camera.matrix_world = mathutils.Matrix.Identity(4)
        trans_vec = mathutils.Vector((0.0, 0.0, -10.00, 1.0))
        _fallback_camera.matrix_world = mathutils.Matrix.Translation(trans_vec)
    else:
        user_mode = view_3d_region.view_perspective
        view_3d_region.view_perspective = "PERSP"
        view_3d_region.update()
        _fallback_camera.matrix_world = view_3d_region.view_matrix
        _fallback_camera.matrix_world.invert()
        view_3d_region.view_perspective = user_mode

    uuid = gen_uuid_obj(_fallback_camera)
    scene_data["camera"] = uuid
    scene_data["objects"].append(uuid)
    process_object(_fallback_camera)

def create_fallback_world(scene_data):
    global _fallback_world

    _fallback_world = bpy.data.worlds.new("FALLBACK_WORLD")
    scene_data["world"] = gen_uuid_obj(_fallback_world)
    process_world(_fallback_world)

def check_scene_data(scene_data, scene):
    # need camera and lamp
    if scene_data["camera"] is None:
        create_fallback_camera(scene_data)
        warn("Missing active camera or wrong active camera object")

    if get_exported_obj_first_rec(scene_data["objects"], "LAMP") is None:
        raise ExportError("Missing lamp", scene)

    if scene_data["world"] is None:
        create_fallback_world(scene_data)
        warn("Missing world or wrong active world object")


def get_exported_obj_first_rec(objects, obj_type = "ALL"):
    for obj in objects:
        obj_data = _export_uuid_cache[obj["uuid"]]

        if obj_type == "ALL" or obj_data["type"] == obj_type:
            return obj_data

        if obj_data["dupli_group"] is not None:
            group_data = _export_uuid_cache[obj_data["dupli_group"]["uuid"]]
            res = get_exported_obj_first_rec(group_data["objects"], obj_type)
            if res is not None:
                return res
    return None

def check_object_data(obj_data, obj):
    # check wind bending vertex colors
    if obj_data["type"] == "MESH" and obj_data["b4a_wind_bending"]:
        detail_bend = obj_data["b4a_detail_bend_colors"]

        m_s_col = obj_data["b4a_main_bend_stiffness_col"]
        l_s_col = detail_bend["leaves_stiffness_col"]
        l_p_col = detail_bend["leaves_phase_col"]
        o_s_col = detail_bend["overall_stiffness_col"]
        colors = m_s_col + l_s_col + l_p_col + o_s_col
        detail_colors = l_s_col + l_p_col + o_s_col

        if colors != "" and (m_s_col == "" or detail_colors != "" and \
                (l_s_col == "" or l_p_col == "" or o_s_col == "")):
            raise ExportError("Wind bending: vertex colors weren't properly assigned", \
                    obj)

        if (m_s_col != "" and not check_vertex_color(obj.data, m_s_col) \
                or l_s_col != "" and not check_vertex_color(obj.data, l_s_col) \
                or l_p_col != "" and not check_vertex_color(obj.data, l_p_col) \
                or o_s_col != "" and not check_vertex_color(obj.data, o_s_col)):
            raise ExportError("Wind bending: not all vertex colors exist", \
                    obj)

    check_obj_particle_systems(obj_data, obj)

def check_obj_particle_systems(obj_data, obj):
    for psys_data in obj_data["particle_systems"]:
        pset_uuid = psys_data["settings"]["uuid"]
        pset_data = _export_uuid_cache[pset_uuid]

        if pset_data["b4a_vcol_from_name"]:
            if not check_vertex_color(obj.data, pset_data["b4a_vcol_from_name"]):
                pset = _bpy_uuid_cache[pset_data["uuid"]]
                raise ExportError("Particle system error", pset, \
                        "The \"" + pset_data["b4a_vcol_from_name"] +
                        "\" vertex color specified in the \"from\" field is " +
                        "missing in the list of the \"" + obj_data["name"]
                        + "\" object's vertex colors")

        if pset_data["render_type"] == "OBJECT":
            dobj_uuid = pset_data["dupli_object"]["uuid"]
            dobj_data = _export_uuid_cache[dobj_uuid]
            dobj = _bpy_uuid_cache[dobj_data["uuid"]]

            if pset_data["b4a_vcol_to_name"]:
                if not check_vertex_color(dobj.data, pset_data["b4a_vcol_to_name"]):
                    raise ExportError("Particle system error", obj, \
                            "The \"" + pset_data["b4a_vcol_to_name"] +
                            "\" vertex color specified in the \"to\" field is " +
                            "missing in the list of the \"" + dobj_data["name"]
                            + "\" object's vertex colors")

        elif pset_data["render_type"] == "GROUP":
            dg_uuid = pset_data["dupli_group"]["uuid"]
            dg_data = _export_uuid_cache[dg_uuid]

            for item in dg_data["objects"]:
                dgobj_data = _export_uuid_cache[item["uuid"]]
                dgobj = _bpy_uuid_cache[dgobj_data["uuid"]]

                if pset_data["b4a_vcol_to_name"]:
                    if not check_vertex_color(dgobj.data, pset_data["b4a_vcol_to_name"]):
                        raise ExportError("Particle system error", obj,
                                "The \"" + pset_data["b4a_vcol_to_name"] +
                                "\" vertex color specified in the \"to\" field is " +
                                "missing in the \"" + dgobj_data["name"] +
                                "\" object (\"" + dg_data["name"] + "\" dupli group)")

def check_vertex_color(mesh, vc_name):
    for color_layer in mesh.vertex_colors:
        if color_layer.name == vc_name:
            return True
    # no found
    return False

def check_uv_layers_limited(mesh, uv_layer_name):
    # Allow special case for empty UV-layer name
    if uv_layer_name == "":
        return True
    index = mesh.uv_textures.find(uv_layer_name)
    return index == 0 or index == 1

def check_mesh_data(mesh_data, mesh):
    for mat in mesh_data["materials"]:
        mat_data = _export_uuid_cache[mat["uuid"]]

        if mat_data["use_vertex_color_paint"] and not mesh.vertex_colors:
            raise ExportError("Incomplete mesh", mesh,
                "Material settings require vertex colors")
        # check dynamic grass vertex colors
        if mat_data["b4a_dynamic_grass_size"] and not \
                check_vertex_color(mesh, mat_data["b4a_dynamic_grass_size"]) \
                or mat_data["b4a_dynamic_grass_color"] and not \
                check_vertex_color(mesh, mat_data["b4a_dynamic_grass_color"]):
            raise ExportError("Incomplete mesh", mesh,
                "Dynamic grass vertex colors required by material settings")

        # ensure that mesh has uvs if one of its materials uses "UV" texture slots
        for tex_slot in mat_data["texture_slots"]:
            if tex_slot["texture_coords"] == "UV" and not mesh_data["uv_textures"]:
                raise ExportError("Incomplete mesh", mesh,
                        "No UV in mesh with UV-textured material")

def check_tex_slot(tex_slot):
    tex = tex_slot.texture
    tc = tex_slot.texture_coords

    if tex and tex.type == "IMAGE" and (tc != "UV" and tc != "NORMAL"
            and tc != "ORCO"):
        raise MaterialError("Wrong texture coordinates type in texture \"" + tex.name + "\".")

def clean_exported_data():
    # NOTE: restore previous selected layers
    global _fallback_camera
    global _fallback_world
    global _fallback_material
    if bpy.data.particles:
        scenes_restore_selected_layers()
    remove_overrided_meshes()
    if _fallback_camera:
        cam_data = _fallback_camera.data
        bpy.data.objects.remove(_fallback_camera)
        bpy.data.cameras.remove(cam_data)
        _fallback_camera = None
    if _fallback_world:
        bpy.data.worlds.remove(_fallback_world)
        _fallback_world = None
    if _fallback_material:
        bpy.data.materials.remove(_fallback_material)
        _fallback_material = None

class B4A_ExportPathGetter(bpy.types.Operator):
    """Get Export Path for blend file"""
    bl_idname = "b4a.get_export_path"
    bl_label = "b4a Get Export Path"

    def execute(self, context):

        print("b4a Export Path = " + get_default_path())

        return {"FINISHED"}


def b4a_export_menu_func(self, context):
    self.layout.operator(B4A_ExportProcessor.bl_idname, \
        text="Blend4Avango (.json)").filepath = get_default_path()

#def check_binaries():
#    if "b4a_bin" not in globals():
#        from . init_validation import bin_invalid_message
#        bpy.app.handlers.scene_update_pre.append(bin_invalid_message)

def register():
    #check_binaries()
    bpy.utils.register_class(B4A_ExportProcessor)
    bpy.utils.register_class(B4A_ExportPathGetter)
    bpy.utils.register_class(ExportErrorDialog)
    bpy.utils.register_class(FileErrorDialog)
    bpy.types.INFO_MT_file_export.append(b4a_export_menu_func)

def unregister():
    bpy.utils.unregister_class(B4A_ExportProcessor)
    bpy.utils.unregister_class(B4A_ExportPathGetter)
    bpy.utils.unregister_class(ExportErrorDialog)
    bpy.utils.unregister_class(FileErrorDialog)
    bpy.types.INFO_MT_file_export.remove(b4a_export_menu_func)
