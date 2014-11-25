import bpy
import mathutils
import math
import os
import cProfile
import bgl

from . import nla_script

# serialize data to json

_OBJECT_PT_constraints = None

class B4A_ScenePanel(bpy.types.Panel):
    bl_label = "Blend4Avango"
    bl_idname = "SCENE_PT_b4a"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    def draw(self, context):
        layout = self.layout

        scene = context.scene
        if scene:

            row = layout.row()
            row.prop(scene, "b4a_enable_ssao", text="Enable SSAO")

            row = layout.row()
            row.prop(scene, "b4a_enable_god_rays", text="Enable God Rays")

            row = layout.row()
            row.prop(scene, "b4a_enable_bloom", text="Enable Bloom")

            split = layout.split()
            col = split.column()

class B4A_WorldPanel(bpy.types.Panel):
    bl_label = "Blend4Avango"
    bl_idname = "WORLD_PT_b4a"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "world"

    def draw(self, context):
        layout = self.layout

        world = context.world
        if world:
            ssao = world.b4a_ssao_settings
            row = layout.row()
            box = row.box()
            col = box.column()
            col.label("SSAO Settings:")
            row = col.row()
            row.prop(ssao, "radius_increase", text="Radius Increase")
            row = col.row()
            row.prop(ssao, "hemisphere", text="Use Hemisphere")
            row = col.row()
            row.prop(ssao, "blur_depth", text="Use Blur Depth Test")
            row = col.row()
            row.prop(ssao, "blur_discard_value", text="Blur Depth Test Discard Value")
            row = col.row()
            row.prop(ssao, "influence", text="Influence")
            row = col.row()
            row.prop(ssao, "dist_factor", text="Distance Factor")
            row = col.row()
            row.label("Samples:")
            row.prop(ssao, "samples", text="Samples", expand=True)
            row = col.row()
            row.prop(ssao, "radius", text="Radius")
            row = col.row()
            row.prop(ssao, "intensity", text="Intensity")
            row = col.row()
            row.prop(ssao, "fallof", text="Fallof")

            god_rays = world.b4a_god_rays_settings
            row = layout.row()
            box = row.box()
            col = box.column()
            col.label("God Rays Settings:")
            row = col.row()
            row.prop(god_rays, "intensity", text="God Rays Intensity")
            row = col.row()
            row.prop(god_rays, "max_ray_length", text="Maximum Ray Length")
            row = col.row()
            row.prop(god_rays, "steps_per_pass", text="Steps Per Pass")

            bloom = world.b4a_bloom_settings
            row = layout.row()
            box = row.box()
            col = box.column()
            col.label("Bloom settings:")
            row = col.row()
            row.prop(bloom, "key", text="Key")
            row = col.row()
            row.prop(bloom, "blur", text="Blur")
            row = col.row()
            row.prop(bloom, "edge_lum", text="Edge Luminance")

class B4A_DataPanel(bpy.types.Panel):
    bl_label = "Blend4Avango"
    bl_idname = "DATA_PT_b4a"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    def draw(self, context):
        layout = self.layout

        cam = context.camera
        if cam:
            row = layout.row(align=True)
            row.prop(cam, "b4a_ms_style", text="Mono/Sterio")
            

class CustomConstraintsPanel(bpy.types.OBJECT_PT_constraints):
    def draw_constraint(self, context, con):

        if con.type == "LOCKED_TRACK":

            layout = self.layout
            box = layout.box()

            box.label("LOCKED_TRACK constraint reserved for " + con.name)

        else:
            global _OBJECT_PT_constraints
            _OBJECT_PT_constraints.draw_constraint(self, context, con)


class B4A_LodAddOperator(bpy.types.Operator):
    bl_idname      = 'lod.add'
    bl_label       = "Add"
    bl_description = "Add new LOD slot"

    def invoke(self, context, event):
        obj = context.active_object

        lods = obj.b4a_lods

        lods.add()

        bpy.ops.object.constraint_add(type="LOCKED_TRACK")

        index = len(lods) - 1
        lods[index].name = "New LOD"

        cons = get_locked_track_constraint(obj, index)

        if obj.b4a_reflective:
            # copy last constraint params to reflection plane constraint
            obj.b4a_refl_plane_index += 1
            cons_refl = get_locked_track_constraint(obj, obj.b4a_refl_plane_index)
            if cons_refl:
                cons_refl.name = cons.name
                cons_refl.target = cons.target

        cons.name = "LOD N " + str(index + 1)
        cons.target = None
        # disable fake LOCKED_TRACK constraint
        cons.mute = True

        return{'FINISHED'}

class B4A_LodRemOperator(bpy.types.Operator):
    bl_idname      = 'lod.remove'
    bl_label       = "Remove"
    bl_description = "Remove selected LOD slot"

    def invoke(self, context, event):
        obj = context.active_object

        lods = obj.b4a_lods

        index = obj.b4a_lod_index
        if len(lods) > 0 and index >= 0:

            lods.remove(index)

            cons = get_locked_track_constraint(obj, index)
            obj.constraints.remove(cons)
            obj.b4a_lod_index -= 1

            # Assign new names based on constraint slot position
            # from 1
            cons_slot_pos = 1
            for cons in obj.constraints:
                if cons.type == "LOCKED_TRACK" and cons_slot_pos <= len(lods) + 1:
                    cons.name = "LOD N " + str(cons_slot_pos)
                    cons_slot_pos += 1

            if obj.b4a_reflective:
                obj.b4a_refl_plane_index -= 1

        return{'FINISHED'}

def add_remove_refl_plane(obj):

    if obj.b4a_reflective:
        #add reflection plane
        bpy.ops.object.constraint_add(type="LOCKED_TRACK")

        lods = obj.b4a_lods
        index = len(lods)
        obj.b4a_refl_plane_index = index

        cons = get_locked_track_constraint(obj, index)
        cons.name = "REFLECTION PLANE"
        # disable fake LOCKED_TRACK constraint
        cons.mute = True

    else:
        #remove reflection plane

        index = obj.b4a_refl_plane_index

        if index >= 0:
            cons = get_locked_track_constraint(obj, index)
            obj.constraints.remove(cons)


def register():
    global _OBJECT_PT_constraints
    bpy.utils.register_class(B4A_LodAddOperator)
    bpy.utils.register_class(B4A_LodRemOperator)

    
    bpy.utils.register_class(B4A_ScenePanel)
    bpy.utils.register_class(B4A_WorldPanel)
    bpy.utils.register_class(B4A_DataPanel)

    _OBJECT_PT_constraints = bpy.types.OBJECT_PT_constraints
    bpy.utils.unregister_class(bpy.types.OBJECT_PT_constraints)
    bpy.utils.register_class(CustomConstraintsPanel)

def unregister():
    global _OBJECT_PT_constraints

    bpy.utils.unregister_class(B4A_LodAddOperator)
    bpy.utils.unregister_class(B4A_LodRemOperator)

    bpy.utils.unregister_class(B4A_ScenePanel)
    bpy.utils.unregister_class(B4A_WorldPanel)
    bpy.utils.unregister_class(B4A_DataPanel)

    bpy.utils.unregister_class(CustomConstraintsPanel)
    bpy.utils.register_class(_OBJECT_PT_constraints)