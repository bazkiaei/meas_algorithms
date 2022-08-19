# This file is part of meas_algorithms.
#
# Developed for the LSST Data Management System.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
import os.path
import tempfile
import unittest
import glob

import numpy as np
from smatch.matcher import sphdist
import astropy.time

import lsst.daf.butler
import lsst.afw.geom as afwGeom
import lsst.afw.table as afwTable
from lsst.daf.butler import DatasetType, DeferredDatasetHandle
from lsst.daf.butler.script import ingest_files
from lsst.meas.algorithms import (ConvertReferenceCatalogTask, ReferenceObjectLoader)
from lsst.meas.algorithms.testUtils import MockReferenceObjectLoaderFromFiles
from lsst.meas.algorithms.loadReferenceObjects import hasNanojanskyFluxUnits
import lsst.utils
import lsst.geom

import ingestIndexTestBase


class ReferenceObjectLoaderTestCase(ingestIndexTestBase.ConvertReferenceCatalogTestBase,
                                    lsst.utils.tests.TestCase):
    """Test case for ReferenceObjectLoader."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Generate a catalog, with arbitrary ids
        inTempDir = tempfile.TemporaryDirectory()
        inPath = inTempDir.name
        skyCatalogFile, _, skyCatalog = cls.makeSkyCatalog(inPath, idStart=25, seed=123)

        cls.skyCatalog = skyCatalog

        # override some field names.
        config = ingestIndexTestBase.makeConvertConfig(withRaDecErr=True, withMagErr=True,
                                                       withPm=True, withPmErr=True)
        # use a very small HTM pixelization depth
        depth = 2
        config.dataset_config.indexer.active.depth = depth
        # np.savetxt prepends '# ' to the header lines, so use a reader that understands that
        config.file_reader.format = 'ascii.commented_header'
        config.n_processes = 1
        config.id_name = 'id'  # Use the ids from the generated catalogs
        cls.repoTempDir = tempfile.TemporaryDirectory()
        repoPath = cls.repoTempDir.name

        # Convert the input data files to our HTM indexed format.
        dataTempDir = tempfile.TemporaryDirectory()
        dataPath = dataTempDir.name
        converter = ConvertReferenceCatalogTask(output_dir=dataPath, config=config)
        converter.run([skyCatalogFile])

        # Make a temporary butler to ingest them into.
        butler = cls.makeTemporaryRepo(repoPath, config.dataset_config.indexer.active.depth)
        dimensions = [f"htm{depth}"]
        datasetType = DatasetType(config.dataset_config.ref_dataset_name,
                                  dimensions,
                                  "SimpleCatalog",
                                  universe=butler.registry.dimensions,
                                  isCalibration=False)
        butler.registry.registerDatasetType(datasetType)

        # Ingest the files into the new butler.
        run = "testingRun"
        htmTableFile = os.path.join(dataPath, "filename_to_htm.ecsv")
        ingest_files(repoPath,
                     config.dataset_config.ref_dataset_name,
                     run,
                     htmTableFile,
                     transfer="auto")

        # Test if we can get back the catalogs, with a new butler.
        butler = lsst.daf.butler.Butler(repoPath)
        datasetRefs = list(butler.registry.queryDatasets(config.dataset_config.ref_dataset_name,
                                                         collections=[run]).expanded())
        handles = []
        for dataRef in datasetRefs:
            handles.append(DeferredDatasetHandle(butler=butler, ref=dataRef, parameters=None))

        cls.datasetRefs = datasetRefs
        cls.handles = handles

        inTempDir.cleanup()
        dataTempDir.cleanup()

    def test_loadSkyCircle(self):
        """Test the loadSkyCircle routine."""
        loader = ReferenceObjectLoader([dataRef.dataId for dataRef in self.datasetRefs],
                                       self.handles)
        center = lsst.geom.SpherePoint(180.0*lsst.geom.degrees, 0.0*lsst.geom.degrees)
        cat = loader.loadSkyCircle(
            center,
            30.0*lsst.geom.degrees,
            filterName='a',
        ).refCat
        # Check that the max distance is less than the radius
        dist = sphdist(180.0, 0.0, np.rad2deg(cat['coord_ra']), np.rad2deg(cat['coord_dec']))
        self.assertLess(np.max(dist), 30.0)

        # Check that all the objects from the two catalogs are here.
        dist = sphdist(180.0, 0.0, self.skyCatalog['ra_icrs'], self.skyCatalog['dec_icrs'])
        inside, = (dist < 30.0).nonzero()
        self.assertEqual(len(cat), len(inside))

        self.assertTrue(cat.isContiguous())
        self.assertEqual(len(np.unique(cat['id'])), len(cat))
        # A default-loaded sky circle should not have centroids
        self.assertNotIn('centroid_x', cat.schema)
        self.assertNotIn('centroid_y', cat.schema)
        self.assertNotIn('hasCentroid', cat.schema)

    def test_loadPixelBox(self):
        """Test the loadPixelBox routine."""
        # This will create a box 50 degrees on a side.
        loaderConfig = ReferenceObjectLoader.ConfigClass()
        loaderConfig.pixelMargin = 0
        loader = ReferenceObjectLoader([dataRef.dataId for dataRef in self.datasetRefs],
                                       self.handles,
                                       config=loaderConfig)
        bbox = lsst.geom.Box2I(corner=lsst.geom.Point2I(0, 0), dimensions=lsst.geom.Extent2I(1000, 1000))
        crpix = lsst.geom.Point2D(500, 500)
        crval = lsst.geom.SpherePoint(180.0*lsst.geom.degrees, 0.0*lsst.geom.degrees)
        cdMatrix = afwGeom.makeCdMatrix(scale=0.05*lsst.geom.degrees)
        wcs = afwGeom.makeSkyWcs(crpix=crpix, crval=crval, cdMatrix=cdMatrix)

        cat = loader.loadPixelBox(bbox, wcs, 'a', bboxToSpherePadding=0).refCat

        # This is a sanity check on the ranges; the exact selection depends
        # on cos(dec) and the tangent-plane projection.
        self.assertLess(np.max(np.rad2deg(cat['coord_ra'])), 180.0 + 25.0)
        self.assertGreater(np.max(np.rad2deg(cat['coord_ra'])), 180.0 - 25.0)
        self.assertLess(np.max(np.rad2deg(cat['coord_dec'])), 25.0)
        self.assertGreater(np.min(np.rad2deg(cat['coord_dec'])), -25.0)

        # The following is to ensure the reference catalog coords are
        # getting corrected for proper motion when an epoch is provided.
        # Use an extreme epoch so that differences in corrected coords
        # will be significant.  Note that this simply tests that the coords
        # do indeed change when the epoch is passed.  It makes no attempt
        # at assessing the correctness of the change.  This is left to the
        # explicit testProperMotion() test below.
        catWithEpoch = loader.loadPixelBox(
            bbox,
            wcs,
            'a',
            bboxToSpherePadding=0,
            epoch=astropy.time.Time(30000, format='mjd', scale='tai')).refCat

        self.assertFloatsNotEqual(cat['coord_ra'], catWithEpoch['coord_ra'], rtol=1.0e-4)
        self.assertFloatsNotEqual(cat['coord_dec'], catWithEpoch['coord_dec'], rtol=1.0e-4)

    def test_filterMap(self):
        """Test filterMap parameters."""
        loaderConfig = ReferenceObjectLoader.ConfigClass()
        loaderConfig.filterMap = {'aprime': 'a'}
        loader = ReferenceObjectLoader([dataRef.dataId for dataRef in self.datasetRefs],
                                       self.handles,
                                       config=loaderConfig)
        center = lsst.geom.SpherePoint(180.0*lsst.geom.degrees, 0.0*lsst.geom.degrees)
        result = loader.loadSkyCircle(
            center,
            30.0*lsst.geom.degrees,
            filterName='aprime',
        )
        self.assertEqual(result.fluxField, 'aprime_camFlux')
        self.assertFloatsEqual(result.refCat['aprime_camFlux'], result.refCat['a_flux'])

    def test_properMotion(self):
        """Test proper motion correction."""
        loaderConfig = ReferenceObjectLoader.ConfigClass()
        loaderConfig.filterMap = {'aprime': 'a'}
        loader = ReferenceObjectLoader([dataRef.dataId for dataRef in self.datasetRefs],
                                       self.handles,
                                       config=loaderConfig)
        center = lsst.geom.SpherePoint(180.0*lsst.geom.degrees, 0.0*lsst.geom.degrees)
        cat = loader.loadSkyCircle(
            center,
            30.0*lsst.geom.degrees,
            filterName='a'
        ).refCat

        # Zero epoch change --> no proper motion correction (except minor numerical effects)
        cat_pm = loader.loadSkyCircle(
            center,
            30.0*lsst.geom.degrees,
            filterName='a',
            epoch=self.epoch
        ).refCat

        self.assertFloatsAlmostEqual(cat_pm['coord_ra'], cat['coord_ra'], rtol=1.0e-14)
        self.assertFloatsAlmostEqual(cat_pm['coord_dec'], cat['coord_dec'], rtol=1.0e-14)
        self.assertFloatsEqual(cat_pm['coord_raErr'], cat['coord_raErr'])
        self.assertFloatsEqual(cat_pm['coord_decErr'], cat['coord_decErr'])

        # One year difference
        cat_pm = loader.loadSkyCircle(
            center,
            30.0*lsst.geom.degrees,
            filterName='a',
            epoch=self.epoch + 1.0*astropy.units.yr
        ).refCat

        self.assertFloatsEqual(cat_pm['pm_raErr'], cat['pm_raErr'])
        self.assertFloatsEqual(cat_pm['pm_decErr'], cat['pm_decErr'])
        for orig, ref in zip(cat, cat_pm):
            self.assertAnglesAlmostEqual(orig.getCoord().separation(ref.getCoord()),
                                         self.properMotionAmt, maxDiff=1.0e-6*lsst.geom.arcseconds)
            self.assertAnglesAlmostEqual(orig.getCoord().bearingTo(ref.getCoord()),
                                         self.properMotionDir, maxDiff=1.0e-4*lsst.geom.arcseconds)
        predictedRaErr = np.hypot(cat["coord_raErr"], cat["pm_raErr"])
        predictedDecErr = np.hypot(cat["coord_decErr"], cat["pm_decErr"])
        self.assertFloatsAlmostEqual(cat_pm["coord_raErr"], predictedRaErr)
        self.assertFloatsAlmostEqual(cat_pm["coord_decErr"], predictedDecErr)

    def test_requireProperMotion(self):
        """Tests of the requireProperMotion config field."""
        loaderConfig = ReferenceObjectLoader.ConfigClass()
        loaderConfig.requireProperMotion = True
        loader = ReferenceObjectLoader([dataRef.dataId for dataRef in self.datasetRefs],
                                       self.handles,
                                       config=loaderConfig)
        center = lsst.geom.SpherePoint(180.0*lsst.geom.degrees, 0.0*lsst.geom.degrees)

        # Test that we require an epoch set.
        msg = 'requireProperMotion=True but epoch not provided to loader'
        with self.assertRaisesRegex(RuntimeError, msg):
            loader.loadSkyCircle(
                center,
                30.0*lsst.geom.degrees,
                filterName='a'
            )


class Version0Version1ReferenceObjectLoaderTestCase(lsst.utils.tests.TestCase):
    """Test cases for reading version 0 and version 1 catalogs."""
    def testLoadVersion0(self):
        """Test reading a pre-written format_version=0 (Jy flux) catalog.
        It should be converted to have nJy fluxes.
        """
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'data',
            'version0',
            'ref_cats',
            'cal_ref_cat'
        )

        filenames = sorted(glob.glob(os.path.join(path, '????.fits')))

        loader = MockReferenceObjectLoaderFromFiles(filenames, name='cal_ref_cat', htmLevel=4)
        result = loader.loadSkyCircle(ingestIndexTestBase.make_coord(10, 20), 5*lsst.geom.degrees, 'a')

        self.assertTrue(hasNanojanskyFluxUnits(result.refCat.schema))
        catalog = afwTable.SimpleCatalog.readFits(filenames[0])
        self.assertFloatsEqual(catalog['a_flux']*1e9, result.refCat['a_flux'])
        self.assertFloatsEqual(catalog['a_fluxSigma']*1e9, result.refCat['a_fluxErr'])
        self.assertFloatsEqual(catalog['b_flux']*1e9, result.refCat['b_flux'])
        self.assertFloatsEqual(catalog['b_fluxSigma']*1e9, result.refCat['b_fluxErr'])

    def testLoadVersion1(self):
        """Test reading a format_version=1 catalog (fluxes unchanged)."""
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'data',
            'version1',
            'ref_cats',
            'cal_ref_cat'
        )

        filenames = sorted(glob.glob(os.path.join(path, '????.fits')))

        loader = MockReferenceObjectLoaderFromFiles(filenames, name='cal_ref_cat', htmLevel=4)
        result = loader.loadSkyCircle(ingestIndexTestBase.make_coord(10, 20), 5*lsst.geom.degrees, 'a')

        self.assertTrue(hasNanojanskyFluxUnits(result.refCat.schema))
        catalog = afwTable.SimpleCatalog.readFits(filenames[0])
        self.assertFloatsEqual(catalog['a_flux'], result.refCat['a_flux'])
        self.assertFloatsEqual(catalog['a_fluxErr'], result.refCat['a_fluxErr'])
        self.assertFloatsEqual(catalog['b_flux'], result.refCat['b_flux'])
        self.assertFloatsEqual(catalog['b_fluxErr'], result.refCat['b_fluxErr'])


class TestMemory(lsst.utils.tests.MemoryTestCase):
    pass


def setup_module(module):
    lsst.utils.tests.init()


if __name__ == "__main__":
    lsst.utils.tests.init()
    unittest.main()