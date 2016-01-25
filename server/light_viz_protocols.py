
import os, sys, logging, types, inspect, traceback, logging, re, json, base64
from time import time

# import RPC annotation
from autobahn.wamp import register as exportRpc

# import paraview modules.
import paraview

from paraview import simple, servermanager
from paraview.web import protocols as pv_protocols

# Needed for:
#    vtkSMPVRepresentationProxy
#    vtkSMTransferFunctionProxy
#    vtkSMTransferFunctionManager
from vtkPVServerManagerRenderingPython import *

# Needed for:
#    vtkSMProxyManager
from vtkPVServerManagerCorePython import *

# Needed for:
#    vtkDataObject
from vtkCommonDataModelPython import *

# =============================================================================
#
# Viewport Size
#
# =============================================================================

# class LightVizViewportSize(pv_protocols.ParaViewWebProtocol):

#     # RpcName: mouseInteraction => viewport.mouse.interaction
#     @exportRpc("light.viz.viewport.size")
#     def updateSize(self, viewId, width, height):
#         view = self.getView(viewId)
#         view.ViewSize = [ width, height ]

# =============================================================================
#
# Dataset management
#
# =============================================================================

class LightVizDatasets(pv_protocols.ParaViewWebProtocol):

    def __init__(self, data_directory):
        super(LightVizDatasets, self).__init__()
        self.basedir = data_directory
        self.datasetMap = {}
        self.dataset = None
        self.datasets = []
        self.activeMeta = None
        self.dataListeners = []
        for filePath in os.listdir(self.basedir):
            indexPath = os.path.join(self.basedir, filePath, 'index.json')
            if os.path.exists(indexPath):
                with open(indexPath, 'r') as fd:
                    metadata = json.loads(fd.read())
                    self.datasets.append(metadata)
                    self.datasetMap[metadata['name']] = { 'path':  os.path.dirname(indexPath), 'meta': metadata }


    def addListener(self, dataChangedInstance):
        self.dataListeners.append(dataChangedInstance)

    def getInput(self):
        return self.dataset

    # RpcName: mouseInteraction => viewport.mouse.interaction
    @exportRpc("light.viz.dataset.list")
    def listDatasets(self):
        return self.datasets

    @exportRpc("light.viz.dataset.thumbnail")
    def getThumbnails(self, datasetName):
        thumbnails = []
        info = self.datasetMap[datasetName]
        if info:
            basePath = info['path']
            for fileName in info['meta']['thumbnails']:
                with open(os.path.join(basePath, fileName), 'rb') as image:
                    thumbnails.append('data:image/%s;base64,%s' % (fileName.split('.')[-1], base64.b64encode(image.read())))

        return thumbnails

    @exportRpc("light.viz.dataset.load")
    def loadDataset(self, datasetName):
        if self.dataset:
            if self.activeMeta is self.datasetMap[datasetName]['meta']:
                return self.activeMeta
            simple.Delete(self.dataset)
            self.dataset = None
            self.datasetRep = None
            self.view = None

        self.activeMeta = self.datasetMap[datasetName]['meta']
        self.dataset = simple.OpenDataFile(os.path.join(self.datasetMap[datasetName]['path'], self.activeMeta['data']['file']))
        self.datasetRep = simple.Show(self.dataset)
        self.view = simple.Render()

        # reset the camera
        simple.ResetCamera(self.view)
        self.view.CenterOfRotation = self.view.CameraFocalPoint
        simple.Render(self.view)

        self.anim = simple.GetAnimationScene()

        # Notify listeners
        for l in self.dataListeners:
            l.dataChanged()

        return self.activeMeta

    @exportRpc("light.viz.dataset.colormap.set")
    def setGlobalColormap(self, presetName):
        for array in self.activeMeta['data']['arrays']:
            rtDataLUT = simple.GetColorTransferFunction(array['name']);
            rtDataLUT.ApplyPreset(presetName, True)
        simple.Render()

    @exportRpc("light.viz.dataset.getstate")
    def getState(self):
        tmp = {
                "opacity": self.datasetRep.Opacity,
                "representation": self.datasetRep.Representation,
                "color": '__SOLID__' if len(self.datasetRep.ColorArrayName[1]) == 0 \
                                     else self.datasetRep.ColorArrayName[1],
                "enabled": self.datasetRep.Visibility == 1,
            }

        if not isinstance(tmp["enabled"], bool):
            tmp["enabled"] = tmp["enabled"][0]

        return tmp

    @exportRpc("light.viz.dataset.opacity")
    def updateOpacity(self, opacity):
        if self.datasetRep:
            self.datasetRep.Opacity = opacity
        return opacity

    @exportRpc("light.viz.dataset.time")
    def updateTime(self, timeIdx):
        self.anim.TimeKeeper.Time = self.anim.TimeKeeper.TimestepValues[timeIdx]
        return self.anim.TimeKeeper.Time

    @exportRpc("light.viz.dataset.representation")
    def updateRepresentation(self, mode):
        if self.datasetRep:
            self.datasetRep.Representation = mode

    @exportRpc("light.viz.dataset.color")
    def updateColorBy(self, field):
        if field == '__SOLID__':
            self.datasetRep.ColorArrayName = ''
        else:
            # Select data array
            vtkSMPVRepresentationProxy.SetScalarColoring(self.datasetRep.SMProxy, field, vtkDataObject.POINT)
            lutProxy = self.datasetRep.LookupTable
            # lutProxy = simple.GetColorTransferFunction(field)
            for array in self.activeMeta['data']['arrays']:
                if array['name'] == field:
                    vtkSMTransferFunctionProxy.RescaleTransferFunction(lutProxy.SMProxy, array['range'][0], array['range'][1], False)

        simple.Render()

    @exportRpc("light.viz.dataset.enable")
    def enableDataset(self, enable):
        self.datasetRep.Visibility = 1 if enable else 0
        simple.Render()

# =============================================================================
#
# Clip management
#
# =============================================================================

class LightVizClip(pv_protocols.ParaViewWebProtocol):

    def __init__(self, dataset_manager):
        super(LightVizClip, self).__init__()
        self.ds = dataset_manager
        self.clipX = None
        self.clipY = None
        self.clipZ = None
        self.representation = None
        self.reprMode = 'Surface'
        self.colorBy = '__SOLID__'
        dataset_manager.addListener(self)

    def dataChanged(self):
        self.updateRepresentation('Surface')
        self.updateColorBy('__SOLID__')
        if self.clipX:
            self.clipX.Input = self.ds.getInput()
            self.updatePosition(50, 50, 50)
            self.updateInsideOut(False, False, False)
        if self.representation:
            self.representation.Visibility = 0

    @exportRpc("light.viz.clip.getstate")
    def getState(self):
        ret = {
            "representation": self.reprMode,
            "color": self.colorBy,
            "enabled": False,
            "xPosition": 0,
            "yPosition": 0,
            "zPosition": 0,
            "xInsideOut": False,
            "yInsideOut": False,
            "zInsideOut": False,
        }
        if self.representation:
            ret["enabled"] = self.representation.Visibility == 1,
        if self.clipX:
            ret["xPosition"] = self.clipX.ClipType.Origin[0]
            ret["yPosition"] = self.clipY.ClipType.Origin[1]
            ret["zPosition"] = self.clipZ.ClipType.Origin[2]
            ret["xInsideOut"] = self.clipX.InsideOut == 1
            ret["yInsideOut"] = self.clipY.InsideOut == 1
            ret["zInsideOut"] = self.clipZ.InsideOut == 1

        if not isinstance(ret["enabled"], bool):
            ret["enabled"] = ret["enabled"][0]

        return ret

    @exportRpc("light.viz.clip.position")
    def updatePosition(self, x, y, z):
        # bounds = self.ds.activeMeta['data']['bounds']
        # if self.clipX:
        #     self.clipX.ClipType.Origin = [float(x)/100.0*(bounds[1]-bounds[0]) + bounds[0], 0, 0]
        # if self.clipY:
        #     self.clipY.ClipType.Origin = [0, float(y)/100.0*(bounds[3]-bounds[2]) + bounds[2], 0]
        # if self.clipZ:
        #     self.clipZ.ClipType.Origin = [0, 0, float(z)/100.0*(bounds[5]-bounds[4]) + bounds[4]]
        if self.clipX:
            self.clipX.ClipType.Origin = [float(x), 0.0, 0.0]
        if self.clipY:
            self.clipY.ClipType.Origin = [0.0, float(y), 0.0]
        if self.clipZ:
            self.clipZ.ClipType.Origin = [0.0, 0.0, float(z)]

    @exportRpc("light.viz.clip.insideout")
    def updateInsideOut(self, x, y, z):
        if self.clipX:
            self.clipX.InsideOut = 1 if x else 0
        if self.clipY:
            self.clipY.InsideOut = 1 if y else 0
        if self.clipZ:
            self.clipZ.InsideOut = 1 if z else 0

    @exportRpc("light.viz.clip.representation")
    def updateRepresentation(self, mode):
        self.reprMode = mode
        if self.representation:
            self.representation.Representation = mode

    @exportRpc("light.viz.clip.color")
    def updateColorBy(self, field):
        self.colorBy = field
        if self.representation:
            if field == '__SOLID__':
                self.representation.ColorArrayName = ''
            else:
                # Select data array
                vtkSMPVRepresentationProxy.SetScalarColoring(self.representation.SMProxy, field, vtkDataObject.POINT)
                lutProxy = self.representation.LookupTable
                # lutProxy = simple.GetColorTransferFunction(field)
                for array in self.ds.activeMeta['data']['arrays']:
                    if array['name'] == field:
                        vtkSMTransferFunctionProxy.RescaleTransferFunction(lutProxy.SMProxy, array['range'][0], array['range'][1], False)

            simple.Render()

    @exportRpc("light.viz.clip.enable")
    def enableClip(self, enable):
        if enable and self.ds.getInput():
            if not self.clipX:
                bounds = self.ds.activeMeta['data']['bounds']
                center = [(bounds[i*2] + bounds[i*2+1])*.05 for i in range(3)]
                self.clipX = simple.Clip(Input=self.ds.getInput())
                self.clipY = simple.Clip(Input=self.clipX)
                self.clipZ = simple.Clip(Input=self.clipY)

                self.clipX.ClipType.Origin = center
                self.clipX.ClipType.Normal = [1, 0, 0]
                self.clipY.ClipType.Origin = center
                self.clipY.ClipType.Normal = [0, 1, 0]
                self.clipZ.ClipType.Origin = center
                self.clipZ.ClipType.Normal = [0, 0, 1]
            else:
                self.clipX.Input = self.ds.getInput()

            if not self.representation:
                self.representation = simple.Show(self.clipZ)
                self.representation.Representation = self.reprMode
                self.updateColorBy(self.colorBy)

            self.representation.Visibility = 1

        if not enable and self.representation:
            self.representation.Visibility = 0

        simple.Render()

    def getOutput(self):
        if not self.clipX:
            bounds = self.ds.activeMeta['data']['bounds']
            center = [(bounds[i*2] + bounds[i*2+1])*.05 for i in range(3)]
            self.clipX = simple.Clip(Input=self.ds.getInput())
            self.clipY = simple.Clip(Input=self.clipX)
            self.clipZ = simple.Clip(Input=self.clipY)

            self.clipX.ClipType.Origin = center
            self.clipX.ClipType.Normal = [1, 0, 0]
            self.clipY.ClipType.Origin = center
            self.clipY.ClipType.Normal = [0, 1, 0]
            self.clipZ.ClipType.Origin = center
            self.clipZ.ClipType.Normal = [0, 0, 1]

        return self.clipZ



# =============================================================================
#
# Contours management
#
# =============================================================================

class LightVizContour(pv_protocols.ParaViewWebProtocol):

    def __init__(self, dataset_manager, clip):
        super(LightVizContour, self).__init__()
        self.ds = dataset_manager
        self.clip = clip
        self.contour = None
        self.representation = None
        self.reprMode = 'Surface'
        self.colorBy = '__SOLID__'
        self.useClippedInput = False
        dataset_manager.addListener(self)

    def dataChanged(self):
        self.updateRepresentation('Surface')
        self.updateColorBy('__SOLID__')
        if self.contour:
            self.contour.Input = self.ds.getInput()
            self.representation.Visibility = 0

    @exportRpc("light.viz.contour.useclipped")
    def setUseClipped(self, useClipped):
        if self.contour:
            if not self.useClippedInput and useClipped:
                self.contour.Input = self.clip.getOutput()
            elif self.useClippedInput and not useClipped:
                self.contour.Input = self.ds.getInput()
        self.useClippedInput = useClipped

    @exportRpc("light.viz.contour.getstate")
    def getState(self):
        ret = {
            "representation": "Surface",
            "color": "__SOLID__",
            "enabled": False,
            "field": '',
            "use_clipped": self.useClippedInput,
            "values": [],
        }
        if self.contour:
            ret["representation"] = self.representation.Representation
            ret["color"] = '__SOLID__' if len(self.representation.ColorArrayName[1]) == 0 \
                                     else self.representation.ColorArrayName[1]
            ret["enabled"] = self.representation.Visibility == 1,
            ret["field"] = self.contour.ContourBy[1]
            ret["values"] = [i for i in self.contour.Isosurfaces]


        if not isinstance(ret["enabled"], bool):
            ret["enabled"] = ret["enabled"][0]

        return ret

    @exportRpc("light.viz.contour.values")
    def updateValues(self, values):
        if self.contour:
            self.contour.Isosurfaces = values

    @exportRpc("light.viz.contour.by")
    def updateContourBy(self, field):
        if self.contour:
            self.contour.ContourBy = field

    @exportRpc("light.viz.contour.representation")
    def updateRepresentation(self, mode):
        self.reprMode = mode
        if self.representation:
            self.representation.Representation = mode

    @exportRpc("light.viz.contour.color")
    def updateColorBy(self, field):
        self.colorBy = field
        if self.representation:
            if field == '__SOLID__':
                self.representation.ColorArrayName = ''
            else:
                # Select data array
                vtkSMPVRepresentationProxy.SetScalarColoring(self.representation.SMProxy, field, vtkDataObject.POINT)
                lutProxy = self.representation.LookupTable
                # lutProxy = simple.GetColorTransferFunction(field)
                for array in self.ds.activeMeta['data']['arrays']:
                    if array['name'] == field:
                        vtkSMTransferFunctionProxy.RescaleTransferFunction(lutProxy.SMProxy, array['range'][0], array['range'][1], False)

            simple.Render()

    @exportRpc("light.viz.contour.enable")
    def enableContour(self, enable):
        if enable and self.ds.getInput():
            inpt = self.ds.getInput() if not self.useClippedInput else self.clip.getOutput()
            if not self.contour:
                self.contour = simple.Contour(Input=inpt, ComputeScalars=1, ComputeNormals=1)
                self.representation = simple.Show(self.contour)
                self.representation.Representation = self.reprMode
                self.updateColorBy(self.colorBy)
            else:
                self.contour.Input = inpt

            self.representation.Visibility = 1

        if not enable and self.representation:
            self.representation.Visibility = 0

        simple.Render()

# =============================================================================
#
# Slice management
#
# =============================================================================

class LightVizSlice(pv_protocols.ParaViewWebProtocol):

    def __init__(self, dataset_manager, clip):
        super(LightVizSlice, self).__init__()
        self.ds = dataset_manager
        self.clip = clip
        self.sliceX = None
        self.sliceY = None
        self.sliceZ = None
        self.representationX = None
        self.representationY = None
        self.representationZ = None
        self.center = None
        self.visible = [0, 0, 0]
        self.enabled = False
        self.reprMode = 'Surface'
        self.colorBy = '__SOLID__'
        self.useClippedInput = False
        dataset_manager.addListener(self)

    def dataChanged(self):
        self.updateRepresentation('Surface')
        self.updateColorBy('__SOLID__')
        if self.sliceX:
            self.sliceX.Input = self.ds.getInput()
            self.sliceY.Input = self.ds.getInput()
            self.sliceZ.Input = self.ds.getInput()
            self.updatePosition(50, 50, 50)
            self.representationX.Representation = 'Surface'
            self.representationY.Representation = 'Surface'
            self.representationZ.Representation = 'Surface'
            self.representationX.ColorArrayName = ''
            self.representationY.ColorArrayName = ''
            self.representationZ.ColorArrayName = ''
            self.representationX.Visibility = 0
            self.representationY.Visibility = 0
            self.representationZ.Visibility = 0
            self.enabled = False

    @exportRpc("light.viz.slice.useclipped")
    def setUseClipped(self, useClipped):
        if self.sliceX:
            if not self.useClippedInput and useClipped:
                for slice in [self.sliceX, self.sliceY, self.sliceZ]:
                    slice.Input = self.clip.getOutput()
            elif self.useClippedInput and not useClipped:
                for slice in [self.sliceX, self.sliceY, self.sliceZ]:
                    slice.Input = self.ds.getInput()
        self.useClippedInput = useClipped


    @exportRpc("light.viz.slice.getstate")
    def getState(self):
        ret = {
            "representation": self.reprMode,
            "color": self.colorBy,
            "enabled": self.enabled,
            "xPosition": 0,
            "yPosition": 0,
            "zPosition": 0,
            "xVisible": self.visible[0] == 1,
            "yVisible": self.visible[1] == 1,
            "zVisible": self.visible[2] == 1,
            "use_clipped": self.useClippedInput,
        }
        if self.center:
            ret['xPosition'] = self.center[0]
            ret['yPosition'] = self.center[1]
            ret['zPosition'] = self.center[2]

        if not isinstance(ret["enabled"], bool):
            ret["enabled"] = ret["enabled"][0]

        return ret

    @exportRpc("light.viz.slice.position")
    def updatePosition(self, x, y, z):
        self.center = [x, y, z]
        if self.sliceX:
            self.sliceX.SliceType.Origin = self.center
        if self.sliceY:
            self.sliceY.SliceType.Origin = self.center
        if self.sliceZ:
            self.sliceZ.SliceType.Origin = self.center

    @exportRpc("light.viz.slice.visibility")
    def updateVisibility(self, x, y, z):
        self.visible = [ 1 if x else 0, 1 if y else 0, 1 if z else 0]
        if self.representationX:
            self.representationX.Visibility = self.visible[0]
        if self.representationY:
            self.representationY.Visibility = self.visible[1]
        if self.representationZ:
            self.representationZ.Visibility = self.visible[2]

    @exportRpc("light.viz.slice.representation")
    def updateRepresentation(self, mode):
        self.reprMode = mode
        if self.representationX:
            self.representationX.Representation = mode
            self.representationY.Representation = mode
            self.representationZ.Representation = mode

    @exportRpc("light.viz.slice.color")
    def updateColorBy(self, field):
        self.colorBy = field
        if self.representationX:
            if field == '__SOLID__':
                self.representationX.ColorArrayName = ''
                self.representationY.ColorArrayName = ''
                self.representationZ.ColorArrayName = ''
            else:
                # Select data array
                vtkSMPVRepresentationProxy.SetScalarColoring(self.representationX.SMProxy, field, vtkDataObject.POINT)
                vtkSMPVRepresentationProxy.SetScalarColoring(self.representationY.SMProxy, field, vtkDataObject.POINT)
                vtkSMPVRepresentationProxy.SetScalarColoring(self.representationZ.SMProxy, field, vtkDataObject.POINT)
                lutProxyX = self.representationX.LookupTable
                lutProxyY = self.representationY.LookupTable
                lutProxyZ = self.representationZ.LookupTable
                # lutProxy = simple.GetColorTransferFunction(field)
                for array in self.ds.activeMeta['data']['arrays']:
                    if array['name'] == field:
                        vtkSMTransferFunctionProxy.RescaleTransferFunction(lutProxyX.SMProxy, array['range'][0], array['range'][1], False)
                        vtkSMTransferFunctionProxy.RescaleTransferFunction(lutProxyY.SMProxy, array['range'][0], array['range'][1], False)
                        vtkSMTransferFunctionProxy.RescaleTransferFunction(lutProxyZ.SMProxy, array['range'][0], array['range'][1], False)

            simple.Render()

    @exportRpc("light.viz.slice.enable")
    def enableSlice(self, enable):
        if enable and self.ds.getInput():
            inpt = self.ds.getInput() if not self.useClippedInput else self.clip.getOutput()
            if not self.sliceX:
                bounds = self.ds.activeMeta['data']['bounds']
                center = self.center
                if center is None:
                    center = [(bounds[i*2] + bounds[i*2+1])*.05 for i in range(3)]
                self.sliceX = simple.Slice(Input=inpt)
                self.sliceY = simple.Slice(Input=inpt)
                self.sliceZ = simple.Slice(Input=inpt)

                self.sliceX.SliceType.Origin = center
                self.sliceX.SliceType.Normal = [1, 0, 0]
                self.sliceY.SliceType.Origin = center
                self.sliceY.SliceType.Normal = [0, 1, 0]
                self.sliceZ.SliceType.Origin = center
                self.sliceZ.SliceType.Normal = [0, 0, 1]

                self.representationX = simple.Show(self.sliceX)
                self.representationY = simple.Show(self.sliceY)
                self.representationZ = simple.Show(self.sliceZ)

                self.updateRepresentation(self.reprMode)
                self.updateColorBy(self.colorBy)
            else:
                self.sliceX.Input = inpt
                self.sliceY.Input = inpt
                self.sliceZ.Input = inpt
            self.representationX.Visibility = self.visible[0]
            self.representationY.Visibility = self.visible[1]
            self.representationZ.Visibility = self.visible[2]

        if not enable and self.representationX:
            self.representationX.Visibility = 0
            self.representationY.Visibility = 0
            self.representationZ.Visibility = 0

        self.enabled = enable
        simple.Render()

# =============================================================================
#
# Multi-Slice management
#
# =============================================================================

class LightVizMultiSlice(pv_protocols.ParaViewWebProtocol):

    def __init__(self, dataset_manager, clip):
        super(LightVizMultiSlice, self).__init__()
        self.ds = dataset_manager
        self.clip = clip
        self.slice = None
        self.representation = None
        self.normal = 0
        self.slicePositions = []
        self.reprMode = "Surface"
        self.colorBy = "__SOLID__"
        self.useClippedInput = False
        dataset_manager.addListener(self)

    def dataChanged(self):
        self.updateRepresentation('Surface')
        self.updateColorBy('__SOLID__')
        if self.slice:
            self.slice.Input = self.ds.getInput()
            self.updatePosition(50, 50, 50)
            self.representation.Representation = 'Surface'
            self.representation.ColorArrayName = ''
            self.representation.Visibility = 0

    @exportRpc("light.viz.contour.useclipped")
    def setUseClipped(self, useClipped):
        if self.slice:
            if not self.useClippedInput and useClipped:
                self.slice.Input = self.clip.getOutput()
            elif self.useClippedInput and not useClipped:
                self.slice.Input = self.ds.getInput()
        self.useClippedInput = useClipped

    @exportRpc("light.viz.mslice.getstate")
    def getState(self):
        ret = {
            'enabled': False,
            'representation': self.reprMode,
            'color': self.colorBy,
            'positions': self.slicePositions,
            'normal': str(self.normal),
            "use_clipped": self.useClippedInput,
        }
        if self.representation:
            ret["enabled"] = True if self.representation.Visibility else False
        return ret


    @exportRpc("light.viz.mslice.normal")
    def updateNormal(self, normalAxis):
        self.normal = int(normalAxis)
        if self.slice:
            normal = [0, 0, 0]
            normal[self.normal] = 1
            self.slice.SliceType.Normal = normal

    @exportRpc("light.viz.mslice.positions")
    def updateSlicePositions(self, positions):
        self.slicePositions = positions;
        if self.slice:
            self.slice.SliceOffsetValues = positions

    @exportRpc("light.viz.mslice.representation")
    def updateRepresentation(self, mode):
        self.reprMode = mode
        if self.representation:
            self.representation.Representation = mode

    @exportRpc("light.viz.mslice.color")
    def updateColorBy(self, field):
        self.colorBy = field
        if self.representation:
            if field == '__SOLID__':
                self.representation.ColorArrayName = ''
            else:
                # Select data array
                vtkSMPVRepresentationProxy.SetScalarColoring(self.representation.SMProxy, field, vtkDataObject.POINT)
                lutProxy = self.representation.LookupTable
                # lutProxy = simple.GetColorTransferFunction(field)
                for array in self.ds.activeMeta['data']['arrays']:
                    if array['name'] == field:
                        vtkSMTransferFunctionProxy.RescaleTransferFunction(lutProxy.SMProxy, array['range'][0], array['range'][1], False)

            simple.Render()

    @exportRpc("light.viz.mslice.enable")
    def enableSlice(self, enable):
        if enable and self.ds.getInput():
            inpt = self.ds.getInput() if not self.useClippedInput else self.clip.getOutput()
            if not self.slice:
                self.slice = simple.Slice(Input=inpt)
                normal = [0, 0, 0]
                normal[self.normal] = 1
                self.slice.SliceType.Normal = normal
                self.slice.SliceOffsetValues = self.slicePositions
                self.representation = simple.Show(self.slice)
                self.representation.Representation = self.reprMode
                self.updateColorBy(self.colorBy)
            else:
                self.slice.Input = inpt
            self.representation.Visibility = 1

        if not enable and self.representation:
            self.representation.Visibility = 0

        simple.Render()
