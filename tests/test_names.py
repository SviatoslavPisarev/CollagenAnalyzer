"""File-name parsing, including the control tags."""
import collagen_core as core
import gelstats as GS

AMF = "112026_3_Collagen_3mgml_NC_0.5nM_inc45min_z_AMF_500Hz_3min_10Vpp_8.5V_2App_500turns_40mTpk_1013"


def test_existing_tags_still_parse():
    assert core.parse_name(AMF, "AMF") == ("112026", "112026_3", "After AMF")
    assert core.parse_amf(AMF) == (500.0, 180)
    assert core.parse_nc(AMF) == 0.5
    assert core.parse_mixing(AMF) == ("incubated", 2700)
    c = core.parse_amf_coil(AMF)
    assert (c["amf_app"], c["coil_turns"], c["amf_b"], c["amf_b_type"]) == (2.0, 500, 40.0, "peak")


def test_sham():
    n = "112026_4_Collagen_3mgml_NC_0.5nM_inc45min_z_sham_1014"
    assert core.parse_sham(n)
    assert core.parse_name(n, "AMF")[2] == "After sham"
    assert core.parse_name("112026_4_x_sham3min_1", "AMF")[2] == "After sham"
    assert core.parse_name("112026_4_x_Sham-3min_1", "AMF")[2] == "After sham"
    assert not core.parse_sham("112026_4_Collagen_shampoo_1")          # not a word boundary
    assert not core.parse_sham("112026_4_x_other-sham-planned_1")      # inside the free note
    assert core.parse_name("112026_4_x_1013", "AMF")[2] == "Before AMF"


def test_no_nc():
    assert core.parse_nc("112026_5_Collagen_3mgml_noNC_z_1") == 0.0
    assert core.parse_nc("112026_5_Collagen_3mgml_no-NCs_z_1") == 0.0
    assert core.parse_nc("112026_5_Collagen_3mgml_NC_0nM_z_1") == 0.0
    assert core.parse_nc("112026_5_Collagen_3mgml_z_1") is None


def test_arms():
    assert GS.file_arm("After AMF", 0.5) == GS.TREATED
    assert GS.file_arm("After AMF", None) == GS.TREATED
    assert GS.file_arm("After AMF", 0.0) == GS.AMF_ONLY
    assert GS.file_arm("After sham", 0.5) == GS.SHAM
    assert GS.file_arm("After sham", 0) == GS.SHAM_NONC
    assert GS.file_arm("Before AMF", 0.5) is None
