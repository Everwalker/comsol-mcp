from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

TASK_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = TASK_ROOT
sys.path.insert(0, str(SOURCE_ROOT))
import comsol_mcp._g2_docs as docs_module
assert Path(docs_module.__file__).resolve() == SOURCE_ROOT / "comsol_mcp" / "_g2_docs.py"

from comsol_mcp._g2_docs import OfflineDocsIndex, infer_version


TEMP_ROOT = Path(tempfile.gettempdir())


class OfflineDocsLiteralSearchTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.base = Path(self.temp.name)
        self.root = self.base / "docs"
        self.root.mkdir()
        self.index = OfflineDocsIndex(self.base / "docs.sqlite", allowed_roots=[self.root])

    def tearDown(self):
        self.index.close()
        self.temp.cleanup()

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def index_root(self):
        return self.index.index(runtime_id="COMSOL64", sources=[str(self.root)])

    def test_query_underscore_and_percent_are_literal(self):
        self.write("edge-literal.txt", "Literal underscore\nedge_case is documented.")
        self.write("edge-decoy.txt", "Underscore decoy\nedgeXcase is documented.")
        self.write("percent-literal.txt", "PercentProbe literal\nThe value is 100% valid.")
        self.write("percent-decoy.txt", "PercentProbe decoy\nThe value is 100 units.")
        self.index_root()

        underscore = self.index.search(query="edge_case", version="6.4")
        self.assertEqual(underscore["status"], "SUCCEEDED")
        self.assertEqual([row["title"] for row in underscore["results"]], ["Literal underscore"])

        percent = self.index.search(query="%", version="6.4", node_type="pErCeNtPrObE")
        self.assertEqual(percent["status"], "SUCCEEDED")
        self.assertEqual([row["title"] for row in percent["results"]], ["PercentProbe literal"])

    def test_query_percent_and_backslash_are_literal_tokens(self):
        self.write("query-percent.txt", "Percent query literal\nThe value is 100% valid.")
        self.write("query-percent-decoy.txt", "Percent query decoy\nThe value is 100 units.")
        self.write("query-route.txt", "Route path literal\nRoute\\Name is documented.")
        self.write("query-route-decoy.txt", "Route path decoy\nRouteXName is documented.")
        self.index_root()

        for query, expected_title in (
            ("100%", "Percent query literal"),
            (r"Route\Name", "Route path literal"),
        ):
            with self.subTest(query=query):
                result = self.index.search(query=query, version="6.4")
                self.assertEqual([row["title"] for row in result["results"]], [expected_title])

    def test_node_type_underscore_percent_and_escape_character_are_literal(self):
        for number, (title, detail) in enumerate((
            ("wall_1", "underscore literal"),
            ("wallX1", "underscore decoy"),
            ("wall%1", "percent literal"),
            ("wallA1", "percent decoy"),
            (r"Route\Name", "escape literal"),
            ("RouteXName", "escape decoy"),
        )):
            self.write(f"nodes/{number}.txt", f"{title}\nNodeProbe {detail}.")
        self.index_root()

        for node_type, expected in (
            ("WALL_1", "wall_1"),
            ("wall%1", "wall%1"),
            (r"route\name", r"Route\Name"),
        ):
            with self.subTest(node_type=node_type):
                result = self.index.search(query="nodeprobe", version="6.4", node_type=node_type)
                self.assertEqual(result["status"], "SUCCEEDED")
                self.assertEqual([row["title"] for row in result["results"]], [expected])

    def test_title_and_content_containment_and_statuses(self):
        self.write("a.txt", "DomainFeature\nPropFeature material selection helpers.")
        self.write("b.txt", "Ordinary title\nPropFeature appears in this body.")
        self.write("c.txt", "NoMatchFeature\nPropFeature unrelated content.")
        self.write("d.txt", "BodyEntry\nNodeBodyProbe appears only in body.")
        self.index_root()

        titled = self.index.search(query="PropFeature", version="6.4", node_type="dOmAiNfEaTuRe")
        self.assertEqual([row["title"] for row in titled["results"]], ["DomainFeature"])
        body_match = self.index.search(query="NodeBodyProbe", version="6.4", node_type="nodebodyprobe")
        self.assertEqual([row["title"] for row in body_match["results"]], ["BodyEntry"])
        self.assertEqual(
            self.index.search(query="PropFeature", version="6.4", node_type="absentfeature")["status"],
            "UNAVAILABLE",
        )
        self.assertEqual(
            self.index.search(query="zzzz-nothing", version="6.4", node_type="DomainFeature")["status"],
            "NOT_FOUND",
        )
        both = self.index.search(query="PropFeature", version="6.4", product="COMSOL",
                                 node_type="NoMatchFeature")
        self.assertEqual([row["title"] for row in both["results"]], ["NoMatchFeature"])

    def test_case_multi_token_version_product_and_sorting_controls(self):
        self.write("COMSOL64/ModelUtil.txt", "ModelUtil\nFile cannot be read.")
        self.write("COMSOL64/Introduction.txt", "Introduction\nRead the file after reboot.")
        self.write("COMSOL64/version.txt", "VersionProbe 6.4\nCurrent API.")
        self.write("COMSOL64/product.txt", "ProductProbe COMSOL\nCOMSOL product API.")
        self.write("COMSOL64/sort-a.txt", "Rank A\nSortProbe alpha.")
        self.write("COMSOL64/sort-b.txt", "Rank B\nSortProbe beta.")
        self.write("COMSOL64/api-punctuation.txt",
                   "API punctuation literal\nModelUtil.getString:src-path is an API token.")
        self.write("COMSOL64/api-punctuation-decoy.txt",
                   "API punctuation decoy\nModelUtil getString src path is an API decoy.")
        self.write("COMSOL63/version.txt", "VersionProbe 6.3\nLegacy API.")
        heat_path = self.write("HeatModule/product.txt", "ProductProbe Heat\nHeat module API.")

        self.index.index(runtime_id="COMSOL64", sources=[
            str(self.root / "COMSOL64"), str(self.root / "COMSOL63")
        ])
        self.index.index(runtime_id="COMSOL64", sources=[str(heat_path)],
                         product="Heat Transfer Module")

        multi = self.index.search(query="FILE cannot be READ", version="6.4")
        self.assertEqual([row["title"] for row in multi["results"]], ["ModelUtil"])
        api_token = self.index.search(query="ModelUtil.getString:src-path", version="6.4")
        self.assertEqual([row["title"] for row in api_token["results"]], ["API punctuation literal"])
        self.assertEqual(
            self.index.search(query="VersionProbe", version="6.4")["results"][0]["title"],
            "VersionProbe 6.4",
        )
        self.assertEqual(
            self.index.search(query="VersionProbe", version="6.3")["results"][0]["title"],
            "VersionProbe 6.3",
        )

        comsol = self.index.search(query="ProductProbe", version="6.4", product="COMSOL")
        heat = self.index.search(query="ProductProbe", version="6.4", product="Heat Transfer Module")
        self.assertEqual([row["product"] for row in comsol["results"]], ["COMSOL"])
        self.assertEqual([row["product"] for row in heat["results"]], ["Heat Transfer Module"])

        ranked = self.index.search(query="SortProbe", version="6.4")
        refs = [row["document_ref"] for row in ranked["results"]]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs, sorted(refs))

    def test_version_install_markers(self):
        self.assertEqual(infer_version("COMSOL64"), "6.4")
        self.assertEqual(infer_version("/Applications/COMSOL63/Multiphysics/doc/help"), "6.3")
        self.assertEqual(infer_version("COMSOL 6.4", "6.3"), "6.4")


if __name__ == "__main__":
    unittest.main()
