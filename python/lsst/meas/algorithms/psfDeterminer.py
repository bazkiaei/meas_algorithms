#
# LSST Data Management System
#
# Copyright 2008-2017  AURA/LSST.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
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
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <https://www.lsstcorp.org/LegalNotices/>.
#

__all__ = ["BasePsfDeterminerConfig", "BasePsfDeterminerTask", "psfDeterminerRegistry"]

import abc

import lsst.pipe.base as pipeBase
import lsst.pex.config as pexConfig


class BasePsfDeterminerConfig(pexConfig.Config):
    """Configuration that is likely to be shared by all PSF determiners

    This is fairly sparse; more fields can be moved here once it is clear they are universal.
    """
    kernelSize = pexConfig.Field(
        doc="radius of the kernel to create, relative to the square root of the stellar quadrupole moments",
        dtype=float,
        default=10.0,
    )
    kernelSizeMin = pexConfig.Field(
        doc="Minimum radius of the kernel",
        dtype=int,
        default=25,
    )
    kernelSizeMax = pexConfig.Field(
        doc="Maximum radius of the kernel",
        dtype=int,
        default=45,
    )


class BasePsfDeterminerTask(pipeBase.Task, metaclass=abc.ABCMeta):
    """Base class for PSF determiners

    Register all PSF determiners with the psfDeterminerRegistry using:
        psfDeterminerRegistry.register(name, class)

    Parameters
    ----------
    config : `lsst.pexConfig.Config`
        Input for configuring the algorithm
    schema : `lsst.afw.table.Schema`
        Schema used for sources; passing a schema allows the
        determiner to reserve a flag field to mark stars used in
        PSF measurement, but some PSF determiners ignore this argument.
    """

    usesMatches = False  # Does the PSF determiner use the "matches" argument in the "run method? Few do.
    ConfigClass = BasePsfDeterminerConfig
    _DefaultName = "psfDeterminer"

    def __init__(self, config, schema=None, **kwds):
        pipeBase.Task.__init__(self, config=config, **kwds)

    @abc.abstractmethod
    def determinePsf(self, exposure, psfCandidateList, metadata=None):
        """Determine a PSF model.

        Parameters
        ----------
        exposure : `lsst.afw.Exposure`
            Exposure containing the psf candidates.
        psdCandidateList : `list` [`lsst.meas.algorithms.PsfCandidate`]
            A sequence of PSF candidates; typically obtained by
            detecting sources and then running them through a star
            selector.
        metadata : `str`
            A place to save interesting items.

        Returns
        -------
        psf : `lsst.afw.detection.Psf`
            The fit PSF.
        cellSet : `lsst.afw.math.SpatialCellSet`
            The spatial cell set used to determine the PSF
        """
        raise NotImplementedError("BasePsfDeterminerTask is abstract, subclasses must override this method")


psfDeterminerRegistry = pexConfig.makeRegistry(
    doc="A registry of PSF determiners (subclasses of BasePsfDeterminerTask)",
)
