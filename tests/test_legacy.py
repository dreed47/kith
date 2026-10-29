"""Old TNG addresses redirect to the new pages."""
import pytest


@pytest.mark.parametrize("old, new", [
    ("/getperson.php?personID=I12&tree=tree1", "/person/I12"),
    ("/getperson.php?personID=i12", "/person/I12"),
    ("/pedigree.php?personID=I7&tree=tree1&generations=5&display=standard", "/pedigree/I7?gens=5"),
    ("/descend.php?personID=I7&generations=3", "/descendants/I7?gens=3"),
    ("/register.php?personID=I7&tree=tree1", "/register/I7"),
    ("/familygroup.php?familyID=F45&tree=tree1", "/family/F45"),
    ("/familychart.php?familyID=F45", "/family/F45/chart"),
    ("/fan.php?personID=I7&generations=6", "/pedigree/I7/fan?gens=6"),
    ("/verticalchart.php?personID=I7", "/pedigree/I7/vertical"),
    ("/ahnentafel.php?personID=I7", "/ahnentafel/I7"),
    ("/descendtables.php?personID=I7", "/descendants/I7/table"),
    ("/relationship.php?primaryID=I1&secondpersonID=I2", "/relationship?a=I1&b=I2"),
    ("/showmedia.php?mediaID=123", "/media/123"),
    ("/showmap.php?cemeteryID=4", "/cemeteries/4"),
    ("/showsource.php?sourceID=s12&tree=tree1", "/sources/S12"),
    ("/browsesources.php", "/sources"),
    ("/placesearch.php?psearch=Clarion%2C%20PA", "/place?name=Clarion,+PA"),
    ("/search.php?mylastname=HOLLIS&lnqualify=equals&mybool=AND", "/surnames/HOLLIS"),
    ("/search.php?myfirstname=Hal&mylastname=Fixture", "/search?q=Hal+Fixture"),
    ("/search.php", "/search"),
    ("/surnames-oneletter.php?firstchar=R", "/surnames"),
    ("/anniversaries.php", "/anniversaries"),
    ("/getperson.php", "/"),  # no person given
    ("/nonsense.php", "/"),
])
def test_old_addresses(admin, old, new):
    r = admin.get(old)
    assert r.status_code == 301 and r.headers["Location"] == new


def test_logged_out_goes_through_login(app):
    r = app.test_client().get("/getperson.php?personID=I12")
    assert r.status_code == 302 and "/login?next=/getperson.php" in r.headers["Location"]
