from factory.adapters.socialmedia.slug import resolve_group_id, slugify_brand


def test_slugify_brand() -> None:
    assert slugify_brand("Enichu") == "enichu"
    assert slugify_brand("Roxabi Media") == "roxabi-media"


def test_resolve_group_id() -> None:
    groups = [{"id": "g1", "name": "Enichu"}, {"id": "g2", "name": "Bully"}]
    assert resolve_group_id(groups, "enichu") == "g1"
    assert resolve_group_id(groups, "missing") is None