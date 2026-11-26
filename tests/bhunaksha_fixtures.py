"""Real Bhunaksha responses captured 2026-09-26 (Tivatghal, Chakur, Latur; survey 126)."""
V = lambda code, value: {"code": code, "value": value, "extraParms": {"hasData": True}}
DISTRICTS = [V("05", "अकोला"), V("07", "अमरावती"), V("26", "अहमदनगर"), V("29", "उस्मानाबाद"),
             V("19", "औरंगाबाद"), V("15", "नांदेड"), V("17", "परभणी"), V("28", "लातूर"),
             V("25", "पुणे")]
TALUKAS_28 = [V("03", "अहमदपुर"), V("10", "उदगीर"), V("07", "औसा"), V("05", "चाकुर"),
              V("04", "जळकोट"), V("09", "देवनी"), V("08", "निलंगा"), V("02", "रेणापुर"),
              V("01", "लातुर"), V("06", "शिरुरअंनतपल")]
VILLAGES_28_05 = [V("272800050353520000", "अजनसोंडा (खु.)"), V("272800050353200000", "अजनसोंडा (बु.)"),
                  V("272800050353340000", "अलगरवाडी"), V("272800050353420000", "तिवघाळ"),
                  V("272800050353410000", "तिवटघाळ")]
GIS = "RVM2805272800050353410000"
PLOT_126 = {
 "area": 27969.5, "plotno": "126", "giscode": GIS, "map_area": 27969.495494755298,
 "plotid": "iSbo_BdVTj-VdPQrz_RLNw", "ownerplots": ["126"],
 "gisinfo": "Category : R Rural, District : 28 लातूर, Taluka : 05 चाकुर, Village : 272800050353410000 तिवटघाळ, ",
 "the_geom": "MULTIPOLYGON(((696470.7938000001 2044113.5611000005,696462.1501000002 2044069.7595000006,696449.7655999996 2044001.8723000009,696444.5743000004 2043971.6750000007,696426.7384000001 2043859.6999999993,696422.4585999995 2043855.3476999998,696366.8525999999 2043870.3990000002,696357.9457999999 2043873.3902000003,696344.0837000003 2043878.0604999997,696323.0718 2043885.0484999996,696355.1105000004 2044117.1634,696357.7680000002 2044128.5715999994,696470.7938000001 2044113.5611000005)))",
 "info": "Survey No. : 126/1\nTotal Area : 1.5700\nPot kharaba : 0.0000\nOwner Name : प्रभाकर तात्याराव बिडवे\nKhata No. : 104\n---------------------------------\nSurvey No. : 126/1\nTotal Area : 1.7500\nPot kharaba : 0.0500\nOwner Name : रमेश तात्याराव बिडवे\nKhata No. : 311\n---------------------------------\nSurvey No. : 126/2\nTotal Area : 0.3800\nPot kharaba : 0.0000\nOwner Name : बाबू रमा कांबळे\nKhata No. : 118\n---------------------------------\nSurvey No. : 126/2\nTotal Area : 0.3800\nPot kharaba : 0.0000\nOwner Name : ऋषीकेश रमेश बीडवे\nKhata No. : 603\n---------------------------------\n",
}
EXT_126 = {"ymin": 18.47594990882092, "xmin": 76.8592980135731, "ymax": 18.478431784358932,
           "xmax": 76.86072311821881, "plotid": "iSbo_BdVTj-VdPQrz_RLNw", "giscode": GIS}


class FakeClient:
    pass


def make_fake():
    import plot_locator as pl

    class Fake(pl.BhunakshaClient):
        calls = []
        def _post(self, path, data):
            self.calls.append((path, dict(data)))
            if path.endswith("ListsAfterLevelGeoref"):
                lvl, codes = int(data["level"]), data["codes"]
                if lvl == 0: return [[V("R", "Rural"), V("U", "Urban")], DISTRICTS]
                if lvl == 2 and codes == "R,28,": return [TALUKAS_28, VILLAGES_28_05[:1]]
                if lvl == 3 and codes == "R,28,05,": return [VILLAGES_28_05, []]
                return []
            if path.endswith("getPlotInfo"):
                return PLOT_126 if data["giscode"] == GIS and data["plotno"] == "126" else None
            if path.endswith("getExtentGeoref"):
                return EXT_126 if data["plotid"] == PLOT_126["plotid"] else None
            return None
    return Fake()
