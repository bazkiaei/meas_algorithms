// -*- LSST-C++ -*-

/* 
 * LSST Data Management System
 * Copyright 2008, 2009, 2010 LSST Corporation.
 * 
 * This product includes software developed by the
 * LSST Project (http://www.lsst.org/).
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 * 
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 * 
 * You should have received a copy of the LSST License Statement and 
 * the GNU General Public License along with this program.  If not, 
 * see <http://www.lsstcorp.org/LegalNotices/>.
 */
 
#if 0 && defined(__ICC)
#pragma warning (push)
#pragma warning (disable: 21)           // type qualifiers are meaningless in this declaration
#pragma warning disable: 68)            // integer conversion resulted in a change of sign
#pragma warning (disable: 279)          // controlling expression is constant
#pragma warning (disable: 304)          // access control not specified ("public" by default)
#pragma warning (disable: 444)          // destructor for base class ... is not virtual
//#pragma warning (pop)
#endif

#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>
#include "lsst/afw/geom.h"
#include "lsst/afw/image.h"
#include "lsst/afw/detection/Psf.h"
#include "lsst/meas/algorithms/FluxControl.h"

namespace afwDetection = lsst::afw::detection;
namespace afwImage = lsst::afw::image;
namespace afwGeom = lsst::afw::geom;

namespace lsst {
namespace meas {
namespace algorithms {
namespace {
/**
 * Implement "Filtered" photometry
 *
 * For details see FilteredFluxControl
 */
class FilteredFlux : public FluxAlgorithm {
public:

    FilteredFlux(FilteredFluxControl const & ctrl, afw::table::Schema & schema) :
        FluxAlgorithm(ctrl, schema, "Value of peak of PSF-filtered image "
        "(an image convolved with its own PSF or an approximate model). "
        "The exposure must contain the PSF. ")
    {}

private:
    
    template <typename PixelT>
    void _apply(
        afw::table::SourceRecord & source,
        afw::image::Exposure<PixelT> const & exposure,
        afw::geom::Point2D const & center
    ) const;

    LSST_MEAS_ALGORITHM_PRIVATE_INTERFACE(FilteredFlux);

};


/**
 * Given an image and a pixel position, return a Flux
 *
 * @raise std::runtime_error if there are no good pixels in footprint of the PSF
 * (centered on the pixel nearest to "center").
 */
template<typename PixelT>
void FilteredFlux::_apply(
    afw::table::SourceRecord & source, 
    afw::image::Exposure<PixelT> const& exposure,
    afw::geom::Point2D const & center
) const {
    source.set(getKeys().flag, true); // say we've failed so that's the result if we throw
    typedef afwImage::Exposure<PixelT>::MaskedImageT MaskedImageT;
    typedef double KernelPixelT;
    typedef afwImage::Image<KernelPixelT> KernelImageT;
    MaskedImageT const mimage = exposure.getMaskedImage();
    PTR(afwDetection::Psf) const psfPtr = exposure.getPsf();

    // compute index and fractional offset of ctrPix: the pixel closest to "center" (the center of the source)
    std::pair<int, double> const xCtrPixIndFrac = mimage.positionToIndex(center.getX(), afwImage::x);
    std::pair<int, double> const yCtrPixIndFrac = mimage.positionToIndex(center.getY(), afwImage::y);

    // compute weight = 1 / sum(PSF^2) for PSF at ctrPix, where PSF is normalized to a sum of 1
    afwGeom::Point2D ctrPixPos(
        mimage.indexToPosition(xCtrPixIndFrac.first, afwImage::x),
        mimage.indexToPosition(yCtrPixIndFrac.first, afwImage::y);
    );
    PTR(const afwMath::Kernel) psfKernelPtr = psfPtr->getLocalKernel(ctrPixPos);
    KernelImageT psfImage(psfKernelPtr.getDimensions());
    psfKernelPtr->computeImage(psfImage, true); // normalize to a sum of 1
    double psfSqSum = 0;
    for (int y = 0, height = psfImage.getHeight(); y < height; ++y) {
        for (afwImage::Image<double>::x_iterator iter = psfImage.row_begin(y), end = psfImage.row_end(y);
            iter != end; ++iter) {
            psfSqSum += (*iter) * (*iter);
        }
    }
    double weight = 1.0 / psfSqSum;

    /*
     * Compute value of image at center of source, as shifted by a fractional pixel to center the source
     * on ctrPix. There is no need to actually shift the image: simply call convolveAtPoint
     * with a suitable warping kernel to compute the shifted image pixel at ctrPix.
     */
    afwMath::SeparableKernel::Ptr warpingKernelPtr = afwMath::makeWarpingKernel(
        static_cast<FilteredFluxControl const &>(getControl()).warpingKernelName;
    );
    double dKerX = xCtrPixIndFrac.second;
    double dKerY = yCtrPixIndFrac.second;
    // warping kernels have even dimension and want the peak to the right of center
    if (dKerX < 0) {
        warpingKernelPtr->setCtrX(warpingKernelPtr->getCtrX() + 1);
    }
    if (dKerY < 0) {
        warpingKernelPtr->setCtrY(warpingKernelPtr->getCtrY() + 1);
    }
    warpingKernelPtr->setKernelParameters(std::make_pair(dKerX, dKerY));
    KernelImageT warpingKernelImage(warpingKernelPtr->getDimensions());
    warpingKernelPtr->computeImage(warpingKernelImage, true);
    typename KernelImageT::const_xy_locator const warpingKernelLoc = warpingKernelImage.xy_at(0,0);

    // Compute imLoc: an image locator that matches kernel locator (0,0) such that
    // image ctrPix overlaps center of warping kernel
    afwGeom::Point2I subimMin = mimage.getXY0() - afwImage.Extent2I(warpingKernelPtr->getCtr());
    typename MaskedImageT::const_xy_locator const mimageLoc = inImage.xy_at(subimMin.getX(), subimMin.getY());
    MaskedImageT::SinglePixel mimageCtrPix = convolveAtAPoint<MaskedImageT, MaskedImageT>(
        mimageLoc, warpingKernelLoc, warpingKernelPtr->getWidth(), warpingKernelPtr->getHeight());

    double flux = mimageCtrPix.image() * weight;
    double var = mimageCtrPix.variance() * weight * weight;
    
    source.set(getKeys().meas, flux);
    source.set(getKeys().err, var);
    source.set(getKeys().flag, false);
}

LSST_MEAS_ALGORITHM_PRIVATE_IMPLEMENTATION(FilteredFlux);

} // anonymous

PTR(AlgorithmControl) FilteredFluxControl::_clone() const {
    return boost::make_shared<FilteredFluxControl>(*this);
}

PTR(Algorithm) FilteredFluxControl::_makeAlgorithm(
    afw::table::Schema & schema,
    PTR(daf::base::PropertyList) const & metadata
) const {
    return boost::make_shared<FilteredFlux>(*this, boost::ref(schema));
}

}}}
