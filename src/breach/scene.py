"""Original procedural geometry; all boxes are depth-tested 3D triangles."""
from panda3d.core import (Geom, GeomNode, GeomTriangles, GeomVertexData,
                          GeomVertexFormat, GeomVertexWriter, NodePath)

def box(name, size, color):
    data = GeomVertexData(name, GeomVertexFormat.getV3n3c4(), Geom.UHStatic)
    vertex = GeomVertexWriter(data, "vertex")
    normal = GeomVertexWriter(data, "normal")
    colors = GeomVertexWriter(data, "color")
    faces = [((1,0,0), [(1,-1,-1),(1,1,-1),(1,1,1),(1,-1,1)]),
             ((-1,0,0), [(-1,1,-1),(-1,-1,-1),(-1,-1,1),(-1,1,1)]),
             ((0,1,0), [(1,1,-1),(-1,1,-1),(-1,1,1),(1,1,1)]),
             ((0,-1,0), [(-1,-1,-1),(1,-1,-1),(1,-1,1),(-1,-1,1)]),
             ((0,0,1), [(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]),
             ((0,0,-1), [(-1,1,-1),(1,1,-1),(1,-1,-1),(-1,-1,-1)])]
    triangles = GeomTriangles(Geom.UHStatic)
    for i, (n, corners) in enumerate(faces):
        for corner in corners:
            vertex.addData3f(*(corner[j] * size[j] / 2 for j in range(3)))
            normal.addData3f(*n)
            colors.addData4f(*color)
        a = i * 4
        triangles.addVertices(a, a+1, a+2)
        triangles.addVertices(a, a+2, a+3)
    geom = Geom(data)
    geom.addPrimitive(triangles)
    node = GeomNode(name)
    node.addGeom(geom)
    return NodePath(node)

def build_scene(render):
    root = render.attachNewNode("foundation-scene")
    def place(name, size, pos, color):
        obj = box(name, size, color)
        obj.reparentTo(root)
        obj.setPos(*pos)
        return obj
    hull = (0.23, 0.34, 0.45, 1)
    place("capital-hull", (16, 46, 8), (0, 85, 4), hull)
    place("capital-deck", (23, 30, 2), (0, 86, 9), hull)
    place("capital-bridge", (7, 9, 7), (0, 96, 13), hull)
    for x in (-11, 11):
        place("engine-pod", (5, 16, 5), (x, 99, 2), hull)
        place("engine-marker", (3, 0.5, 3), (x, 90.7, 2), (0.1, 0.8, 1, 1))
    for x in (-14, 14):
        place("dock-pillar", (1, 2, 22), (x, 37, 0), (0.2, 0.75, 0.7, 1))
    for z in (-11, 11):
        place("dock-crossbar", (29, 2, 1), (0, 37, z), (0.2, 0.75, 0.7, 1))
    for x, y, z in [(-23, 56, 7), (21, 63, -5), (27, 100, 19)]:
        fighter = place("fighter-placeholder", (5, 4, 0.8), (x, y, z), (0.95, 0.4, 0.16, 1))
        fighter.setHpr(20, 10, 25)
    import random
    rng = random.Random(14)
    for i in range(65):
        place(f"marker-{i}", (0.25, 0.25, 0.25),
              (rng.uniform(-100, 100), rng.uniform(130, 230), rng.uniform(-60, 70)),
              (0.6, 0.7, 0.85, 1))
    # All-direction navigation references: turning away must not erase every
    # orientation cue. Static world geometry, never a camera/chase transform.
    import math
    for i in range(360):
        z = rng.uniform(-1, 1)
        azimuth = rng.uniform(0, 2*math.pi)
        radius = rng.uniform(400, 650)
        radial = math.sqrt(1-z*z)
        value = rng.uniform(.25, .7)
        star = place(f"flight-reference-{i}", (2, 2, 2),
                     (radius*radial*math.cos(azimuth),
                      radius*radial*math.sin(azimuth), radius*z),
                     (value*.8, value*.9, value, 1))
        star.setLightOff()
    return root
