"""The LGPL source tools' pins agree with the licence notes the archive ships."""

from pathlib import Path

from tools import build_libquadmath_source as quadmath
from tools import build_libsndfile_source as sndfile

LICENCES = Path(__file__).resolve().parent.parent / 'THIRD_PARTY_LICENSES'


def test_every_libquadmath_build_is_described_in_its_source_note():
    note = (LICENCES / 'libquadmath' / 'SOURCE.txt').read_text(encoding='utf-8')
    for build in quadmath.BUILDS:
        assert build.build_id in note
        assert build.rpm_release in note
        assert build.srpm_name in note


def test_the_libsndfile_note_names_the_binary_and_every_component():
    note = (LICENCES / 'libsndfile' / 'SOURCE.txt').read_text(encoding='utf-8')
    assert sndfile.BINARIES_COMMIT in note
    assert sndfile.SHIPPED in note
    for component in sndfile.COMPONENTS:
        assert component.version in note


def test_every_libsndfile_component_has_its_licence_text():
    for component in sndfile.COMPONENTS:
        for name in component.licence_files:
            assert (LICENCES / 'libsndfile' / component.name / name).is_file(), (component.name, name)


def test_an_include_outside_the_kept_files_is_reported():
    sources = {'libquadmath/printf/gmp-impl.h': b'#include "../../libgcc/longlong.h"\n',
               'libquadmath/math/x.c': b'#include "../quadmath-imp.h"\n'}
    kept = {'libquadmath/printf/gmp-impl.h', 'libquadmath/math/x.c', 'libquadmath/quadmath-imp.h'}
    assert quadmath.missing_includes(sources, kept) == [
        'libquadmath/printf/gmp-impl.h -> libgcc/longlong.h']
    assert quadmath.missing_includes(sources, kept | {'libgcc/longlong.h'}) == []
