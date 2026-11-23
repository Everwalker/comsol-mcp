from __future__ import annotations

import unittest
from unittest import mock

from comsol_mcp import _tools_physics


class _FeatureStore:
    def __init__(self, nodes=None):
        self.nodes = dict(nodes or {})

    def tags(self):
        return list(self.nodes)

    def __call__(self, tag=None):
        return self if tag is None else self.nodes[tag]

    def create(self, tag, feature_type):
        self.nodes[tag] = _PropertyNode(feature_type)


class _PropertyNode:
    def __init__(self, type_id="Physics"):
        self.type_id = type_id
        self.properties = {}

    def set(self, name, value):
        self.properties[name] = value


class _Physics(_PropertyNode):
    def __init__(self):
        super().__init__()
        self.features = _FeatureStore({"bc1": _PropertyNode("TemperatureBoundary")})

    def feature(self, tag=None):
        return self.features(tag)


class _PhysicsStore:
    def __init__(self):
        self.nodes = {"ht": _Physics()}

    def tags(self):
        return list(self.nodes)

    def __call__(self, tag=None):
        return self if tag is None else self.nodes[tag]


class _Component:
    def __init__(self):
        self.physics_nodes = _PhysicsStore()

    def physics(self, tag=None):
        return self.physics_nodes(tag)


class _Model:
    def __init__(self):
        self.component_node = _Component()
        self.java = type(
            "Java",
            (),
            {"component": lambda _self, _tag: self.component_node},
        )()


class PhysicsFeatureParentPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.model = _Model()
        self.run_tool_patch = mock.patch.object(
            _tools_physics, "_run_tool", lambda _name, callback: callback()
        )
        self.model_patch = mock.patch.object(
            _tools_physics, "_require_visible_main", lambda _name: self.model
        )
        self.run_tool_patch.start()
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.addCleanup(self.run_tool_patch.stop)

    def test_update_empty_tag_targets_parent_interface(self):
        _tools_physics.update_physics_feature("comp1", "ht", "", '{"T0":"293.15[K]"}')

        physics = self.model.component_node.physics("ht")
        self.assertEqual(physics.properties, {"T0": "293.15[K]"})
        self.assertEqual(physics.features("bc1").properties, {})

    def test_update_targets_named_child(self):
        _tools_physics.update_physics_feature("comp1", "ht", "bc1", '{"T0":"303.15[K]"}')

        physics = self.model.component_node.physics("ht")
        self.assertEqual(physics.features("bc1").properties, {"T0": "303.15[K]"})
        self.assertEqual(physics.properties, {})

    def test_create_requires_a_child_tag(self):
        with self.assertRaisesRegex(ValueError, "feature_tag is required"):
            _tools_physics.create_physics_feature(
                "comp1", "ht", "", "TemperatureBoundary", "[]"
            )

        physics = self.model.component_node.physics("ht")
        self.assertNotIn("", physics.features.tags())


if __name__ == "__main__":
    unittest.main()
