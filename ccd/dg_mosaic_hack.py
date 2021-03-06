#!/usr/bin/env python2.6
# __BEGIN_LICENSE__
#  Copyright (c) 2009-2013, United States Government as represented by the
#  Administrator of the National Aeronautics and Space Administration. All
#  rights reserved.
#
#  The NGT platform is licensed under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance with the
#  License. You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# __END_LICENSE__

# To do: There is no need to round the corners of the images
# which are combined into the mosaic. May improve accuracy
# that way. This change would need very careful testing.

import os, sys, string, subprocess, math, re, optparse, time, copy, tempfile
from datetime import datetime, timedelta

# Prepend to system PATH

def format_microsec(microsec):
    # Ensure that we have 6 digits in a microsecond. Pre-pad with
    # zeros if necessary.
    if len(microsec) > 6:
        raise Exception('InputError', "The microsecond value: " + microsec
                        + " has more than 6 digits.")

    microsec  = (6 - len(microsec)) * '0' + microsec
    return microsec

try:
    import xml.etree.ElementTree as ET
except ImportError:
    # python 2.4
    import elementtree.ElementTree as ET

if hasattr(datetime, 'strptime'):
    def parse_time(date_string, fmt):
        return datetime.strptime(date_string, fmt)
else:
    # python 2.4
    def parse_time(date_str, fmt):
        m = re.match('^(.*?)T(.*?)\.(\d+)Z', date_str)
        if m:
            date_str  = m.group(1) + " " + m.group(2)
            microsec  = format_microsec(m.group(3))
        else:
            raise Exception('InputError', "Could not match string: " + date_str)

        m = re.match('^(.*?)T(.*?)\.(.*?)Z', fmt)
        if m:
            fmt = m.group(1) + " " + m.group(2)
        else:
            raise Exception('InputError', "Could not match string: " + fmt)

        parsed = datetime(*(time.strptime(date_str, fmt)[0:6]))
        parsed = parsed.replace(microsecond=int(microsec))

        return parsed

class Usage(Exception):
    def __init__(self, msg):
        self.msg = msg

# We assume that the boxes are products of closed intervals, [a, b] x
# [c, d]. As such, the box [0, 0] x [0, 0] of width 0 is not empty.
class BBox:
    def __init__(self,
                 minx =  sys.float_info.max, miny =  sys.float_info.max,
                 maxx = -sys.float_info.max, maxy = -sys.float_info.max):
        self.minx = minx
        self.miny = miny
        self.maxx = maxx
        self.maxy = maxy

    def empty(self):
        return ( self.maxx < self.minx ) or ( self.maxy < self.miny )

    def expand_with_box(self, box):
        if box.empty(): return
        if self.empty():
            self.minx = box.minx
            self.miny = box.miny
            self.maxx = box.maxx
            self.maxy = box.maxy
            return
        if box.minx < self.minx: self.minx = box.minx
        if box.miny < self.miny: self.miny = box.miny
        if box.maxx > self.maxx: self.maxx = box.maxx
        if box.maxy > self.maxy: self.maxy = box.maxy

    def add_point(self, pt):
        self.expand_with_box(BBox(pt[0], pt[1], pt[0], pt[1]))

    def extend (self, val):
        # Note: val can be negative
        if self.empty(): return
        self.minx = self.minx - val
        self.miny = self.miny - val
        self.maxx = self.maxx + val
        self.maxy = self.maxy + val
        if self.empty():
            # Ensure all empty boxes are identical. For some reason
            # the line self = BBox() does not work.  So need to assign
            # the members one by one.
            b = BBox()
            self.minx = b.minx
            self.miny = b.miny
            self.maxx = b.maxx
            self.maxy = b.maxy

    def width(self):
        if self.empty(): return 0
        return self.maxx - self.minx

    def height(self):
        if self.empty(): return 0
        return self.maxy - self.miny

    def __repr__(self):
        return "BBox %f %f -> %f %f" % (self.minx,self.miny,self.maxx,self.maxy)

# Create an object made of a string, a 2D vector, a box, and a set of
# lon-lat corners. Sort an array of such objects by the min coordinate
# of the box.
class StVecBoxCr:
    def __init__(self, st, vec, box, corners):
        self.st      = st
        self.vec     = vec
        self.box     = box
        self.corners = corners
        
def minyCompare(P, Q):
    return P.box.miny - Q.box.miny
def sort_by_miny(args, orig_sizes, placements, ll_corners):
    S = []
    for i in range(len(args)):
        S.append(StVecBoxCr(copy.deepcopy(args[i]),
                            copy.deepcopy(orig_sizes[i]),
                            copy.deepcopy(placements[i]),
                            copy.deepcopy(ll_corners[i])
                            ))
    S.sort(minyCompare)
    s_args = []; s_orig_sizes = []; s_placements = []; s_ll_corners = []
    for i in range(len(S)):
        s_args.append(S[i].st)
        s_orig_sizes.append(S[i].vec)
        s_placements.append(S[i].box)
        s_ll_corners.append(S[i].corners)
    return (s_args, s_orig_sizes, s_placements, s_ll_corners)

def set_or_wipe_image_tags(IMAGE, avg_meanproductgsd, avg_meancollectedgsd):

    # Wipe most image tags as they differ from image to image. Set the
    # MEANPRODUCTGSD and MEANCOLLECTEDGSD tags as the average of
    # individual values of this tag.

    keep_tags = set(["SATID", "MODE", "SCANDIRECTION", "CATID", "TLCTIME",
                     "NUMTLC", "TLCLISTList", "FIRSTLINETIME", "AVGLINERATE",
                     "EXPOSUREDURATION", "MEANPRODUCTGSD", "MEANCOLLECTEDGSD",
                     "CLOUDCOVER", "RESAMPLINGKERNEL", "POSITIONKNOWLEDGESRC",
                     "ATTITUDEKNOWLEDGESRC", "REVNUMBER"]);

    # Read existing tags.
    tags = []
    for i in range(len(IMAGE)):
        tag = IMAGE[i].tag
        tags.append(tag)

    # Wipe some of the tags.
    for tag in tags:
        if not tag in keep_tags:
            IMAGE.remove( IMAGE.find (tag) )

    # Set some tags to average value.
    if IMAGE.find("MEANPRODUCTGSD") is not None:
        IMAGE.find("MEANPRODUCTGSD").text   = str(avg_meanproductgsd)
    if IMAGE.find("MEANCOLLECTEDGSD") is not None:
        IMAGE.find("MEANCOLLECTEDGSD").text = str(avg_meancollectedgsd)

def get_corners(A, B, indices):
    # Each corner of C will be picked either from A or from B,
    # depending on the value of 'indices' for that corner.
    C=[]
    for i in range(len(indices)):
        j = indices[i]
        if j == 0:
            C.append(A[i])
        else:
            C.append(B[i])
            
    return C

def signed_triangle_area(P, Q, R):
    return ( (Q[0]-P[0])*(R[1]-Q[1])-(Q[1]-P[1])*(R[0]-Q[0]) )/2.0

def find_combined_box(A, B):

    # Given two boxes, A and B, find the rough box containing both A and B.
    # Both A and B is a vector of 4 points, each point having two
    # coordinates, x and y.

    # Each of the corners of the new box will be one of the corresponding
    # corners of either A or B. Pick the new corners as to maximize
    # the area of the obtained box.

    if len(A) == 0: return B
    if len(A) != 4 or len(B) != 4:
        raise Exception('Error', "A box is supposed to have 4 corners.")
        
    max_area = 0
    max_indices = []
    for a in range(2):
        for b in range(2):
            for c in range(2):
                for d in range(2):
                    indices = [a, b, c, d]
                    C = get_corners(A, B, indices)
                    area = abs( signed_triangle_area(C[0], C[1], C[2]) +
                                signed_triangle_area(C[2], C[3], C[0]))
                    if area > max_area:
                        max_area = area
                        max_indices = indices
                        
    return get_corners(A, B, max_indices)


def adjust_camera_corners(BAND_P, all_ll_corners):

    # The lon-lat box for the combined camera is the union of the
    # lon-lat boxes for all the cameras.

    # Wipe unneeded tags. For that, first store the tags, then wipe
    # them.
    wipe_tags = set(["ULHAE", "URHAE","LRHAE","LLHAE"]);
    tags = []
    for i in range(len(BAND_P)):
        tag = BAND_P[i].tag
        tags.append(tag)
    for tag in tags:
        if tag in wipe_tags:
            BAND_P.remove( BAND_P.find (tag) )

    # Each camera is a rectangle. Find the rough bounding box of all rectangles.
    out_corners = []
    for corner_vec in all_ll_corners:
        out_corners = find_combined_box(out_corners, corner_vec)

    BAND_P.find("ULLON").text = str(out_corners[0][0])
    BAND_P.find("ULLAT").text = str(out_corners[0][1])

    BAND_P.find("URLON").text = str(out_corners[1][0])
    BAND_P.find("URLAT").text = str(out_corners[1][1])

    BAND_P.find("LRLON").text = str(out_corners[2][0])
    BAND_P.find("LRLAT").text = str(out_corners[2][1])

    BAND_P.find("LLLON").text = str(out_corners[3][0])
    BAND_P.find("LLLAT").text = str(out_corners[3][1])

# Provide the image files that make up the composite. We assume the XML
# files are in the same directory and end with extension ".xml" or ".XML".
def read_xml( filename, options):

    tree = ET.parse( filename )
    root = tree.getroot()
    IMD  = root.find("IMD")
    GEO  = root.find("GEO")
    EPH  = root.find("EPH")
    ATT  = root.find("ATT")
    RPB  = root.find("RPB")

    IMAGE = IMD.find("IMAGE")
    tlctime = parse_time(IMAGE.find("TLCTIME").text.strip(),
                         "%Y-%m-%dT%H:%M:%S.%fZ")
    tlclist = []
    for tlc_item in IMAGE.find("TLCLISTList").findall("TLCLIST"):
        tokens = tlc_item.text.strip().split(' ')
        tlclist.append([float(tokens[0]), float(tokens[1])])
    if len(tlclist) == 1:
        direction = 1.0
        if IMAGE.find("SCANDIRECTION").text.lower() != "forward":
            direction = -1.0
        linerate = float(IMAGE.find("AVGLINERATE").text)
        tlclist.append([(linerate+tlclist[0][0]), (tlclist[0][1]+direction)])
    firstlinetime = parse_time(IMAGE.find("FIRSTLINETIME").text,
                               "%Y-%m-%dT%H:%M:%S.%fZ")

    if IMAGE.find("MEANPRODUCTGSD") is not None:
        meanproductgsd   = float(IMAGE.find("MEANPRODUCTGSD").text)
    else:
        meanproductgsd = None
    if IMAGE.find("MEANCOLLECTEDGSD") is not None:
        meancollectedgsd = float(IMAGE.find("MEANCOLLECTEDGSD").text)
    else:
        meancollectedgsd = None
        
    image_size = []
    image_size.append( int(IMD.find("NUMCOLUMNS").text) )
    image_size.append( int(IMD.find("NUMROWS").text) )

    llbox = BBox()
    htbox = BBox()
    ll_corners = []

    # The longitude-lattitude box. Note: Later when we create
    # the adjusted box we will traverse the corners in
    # exactly the same order as here, so this and that
    # code blocks must be kept consistent.
    try:
        ullon = float(IMD.find("BAND_P").find("ULLON").text)
        ullat = float(IMD.find("BAND_P").find("ULLAT").text)
        llbox.add_point([ullon, ullat])
        ll_corners.append([ullon, ullat])

        urlon = float(IMD.find("BAND_P").find("URLON").text)
        urlat = float(IMD.find("BAND_P").find("URLAT").text)
        llbox.add_point([urlon, urlat])
        ll_corners.append([urlon, urlat])

        lrlon = float(IMD.find("BAND_P").find("LRLON").text)
        lrlat = float(IMD.find("BAND_P").find("LRLAT").text)
        llbox.add_point([lrlon, lrlat])
        ll_corners.append([lrlon, lrlat])

        lllon = float(IMD.find("BAND_P").find("LLLON").text)
        lllat = float(IMD.find("BAND_P").find("LLLAT").text)
        llbox.add_point([lllon, lllat])
        ll_corners.append([lllon, lllat])
    except Exception, e:
        raise Exception('InputError', "File " + filename
                        + " lacks longitude/lattitude information.")

    if not options.skip_rpc_gen:
        # The height range, [ht_min, ht_max]. We store it as a 2D box
        # as to not create an interval class.
        try:
            ht_scale = float(RPB.find("IMAGE").find("HEIGHTSCALE").text)
            ht_offset = float(RPB.find("IMAGE").find("HEIGHTOFFSET").text)
            min_ht = -ht_scale + ht_offset
            max_ht =  ht_scale + ht_offset
            htbox = BBox(min_ht, min_ht, max_ht, max_ht)
        except Exception, e:
            raise Exception('InputError', "File " + filename
                            + " lacks RPC model information.")

    DET_ARRAY = GEO.find("DETECTOR_MOUNTING").find("BAND_P").find("DETECTOR_ARRAY")
    pitch     = float(DET_ARRAY.find("DETPITCH").text)
    originx   = DET_ARRAY.find("DETORIGINX").text
    pd        = GEO.find("PRINCIPAL_DISTANCE").find("PD").text

    # Note: We concatenate all eph values into one single array,
    # the proper structure would be an array of arrays.
    eph_list = []
    for eph_item in EPH.find("EPHEMLISTList").findall("EPHEMLIST"):
        tokens = eph_item.text.strip().split(' ')
        eph_list.extend(tokens)

    # Note: We concatenate all att values into one single array,
    # the proper structure would be an array of arrays.
    att_list = []
    for att_item in ATT.find("ATTLISTList").findall("ATTLIST"):
        tokens = att_item.text.strip().split(' ')
        att_list.extend(tokens)

    return [tlctime, tlclist, firstlinetime, image_size, pitch,
            originx, pd, eph_list, att_list, llbox, htbox, ll_corners,
            meanproductgsd, meancollectedgsd]

def tlc_time_lookup( tlctime, tlclist, pixel_y ):
    fraction = (pixel_y - tlclist[0][0]) / (tlclist[1][0] - tlclist[0][0])
    seconds_off = fraction * ( tlclist[1][1] - tlclist[0][1]) + tlclist[0][1]
    return tlctime + timedelta(seconds=seconds_off)

def tlc_pixel_lookup( tlctime, tlclist, time ):
    difference = time - tlctime
    seconds_since_ref = difference.microseconds /  10.0**6 + difference.seconds + difference.days*24.0*3600.0
    fraction = ( seconds_since_ref - tlclist[0][1] ) / (tlclist[1][1] - tlclist[0][1]);
    return fraction * ( tlclist[1][0] - tlclist[0][0] ) + tlclist[0][0]

# Get the xml file name from the .ntf or .tif file name. The xml file
# must exist and end in either '.xml' or '.XML'.
def xml_name( filename ):
    path, ext = os.path.splitext(filename)
    
    m = re.match('^(.*?)(-BROWSE)', path)
    if m:
        path = m.group(1)
    m = re.match('^(.*?)(_crop)', path)
    if m:
        path = m.group(1)

    xml_file_l = path + ".xml"
    xml_file_u = path + ".XML"

    if os.path.isfile(xml_file_l):
        xml_file = xml_file_l
    elif os.path.isfile(xml_file_u):
        xml_file = xml_file_u
    else:
        raise Exception('InputError', 'Cannot find any of: '
                        + xml_file_l + ' ' + xml_file_u)

    return xml_file

def run_cmd( cmd, options ):
    if options.dryrun or options.verbose: print cmd
    if options.dryrun: return
    code = subprocess.call(cmd, shell=True)
    if code:
        raise Exception('ProcessError', 'Non zero return code ('+str(code)+')')

def die(msg, code=-1):
    print >>sys.stderr, msg
    sys.exit(code)

def scale_xml_element(elem, scale):
    tokens = elem.text.strip().split(' ')
    for i in range(len(tokens)):
        tokens[i] = str(float(tokens[i]) * scale)
    elem.text = " ".join(tokens)

def main():

    try:
        try:
            usage = '''dg_mosaic [--help][--gdal-dir dir] <all ntf or tif files that make up the observation>

  [ASP 2.3.0_post]'''

            parser = optparse.OptionParser(usage=usage)
            parser.set_defaults(dryrun=False)
            parser.set_defaults(preview=False)
            parser.set_defaults(gdaldir="")
            parser.set_defaults(reduce_percent=100.0)
            parser.set_defaults(output_prefix="")
            parser.set_defaults(input_nodata_value=None)
            parser.set_defaults(output_nodata_value=None)
            parser.add_option("--gdal-dir", dest="gdaldir",
                              help="Directory where the GDAL tools are. If not provided, we get them from the environment.")
            parser.add_option("--reduce-percent", dest="reduce_percent", type="float",
                              help="Render a reduced resolution image and xml based on this percentage.")
            parser.add_option("--output-prefix", dest="output_prefix", type="str",
                              help="The prefix for the output .tif and .xml files.")
            parser.add_option("--input-nodata-value", dest="input_nodata_value", type="float",
                              help="Nodata value to use on input; input pixel values less than or equal to this are considered invalid.")
            parser.add_option("--output-nodata-value", dest="output_nodata_value", type="float",
                              help="Nodata value to use on output.")
            parser.add_option('--skip-rpc-gen', dest='skip_rpc_gen', default=False,
                              action='store_true', help="Skip RPC model generation")
            parser.add_option("--rpc-penalty-weight", dest="penalty_weight", type="float",
                              default=0.1,
                              help="The weight to use to penalize higher order RPC coefficients when generating the combined RPC model. Higher penalty weight results in smaller such coefficients.")
            parser.add_option("--preview", dest="preview",
                              action="store_true",
                              help="Render a small 8 bit png of the input for preview.")
            parser.add_option("-n", "--dry-run", dest="dryrun",
                              action="store_true",
                              help="Make the calculations, but just print out the commands.")
            parser.add_option("-v", "--verbose", dest="verbose",
                              action="store_true", help="Increase verbosity.")
            # Debug option. Skip the part where we create the tif file,
            # that is quite time-consuming.
            parser.add_option('--skip-tif-gen', dest='skip_tif_gen', default=False,
                              action='store_true', help=optparse.SUPPRESS_HELP)

            (options, args) = parser.parse_args()

            # Transform 50.0 into just 50
            if options.reduce_percent == int(options.reduce_percent):
                options.reduce_percent = int(options.reduce_percent)

            if not args:
                parser.print_help()
                die('\nERROR: Need .ntf or .tif files.', code=2)

            if len(options.gdaldir): options.gdaldir += "/"

        except optparse.OptionError, msg:
            raise Usage(msg)

        sep = ","

        if options.output_prefix == "":
            options.output_prefix = args[0].split('-')[-1].split('.')[0]
        if options.output_prefix == "":
            die('\nERROR: Empty output prefix. Please set it via the ' + \
                '--output-prefix option', code=2)
        out_dir=os.path.dirname(options.output_prefix)
        if len(out_dir)==0: out_dir='.'
        if not os.path.exists(out_dir): os.mkdir(out_dir)

        all_ll_corners = []

        # We reference everything from the perspective of the first
        # image. So lets load it up first into our special variables.
        avg_meanproductgsd   = 0; num_meanproductgsd   = 0
        avg_meancollectedgsd = 0; num_meancollectedgsd = 0
        [ref_tlctime, ref_tlclist, ref_firstlinetime, ref_image_size,
         ref_pitch, ref_originx, ref_pd, ref_eph_list, ref_att_list,
         ref_llbox, ref_htbox, ll_corners,
         meanproductgsd, meancollectedgsd] \
         = read_xml( xml_name(args[0]), options )
        all_ll_corners.append(ll_corners)

        if meanproductgsd is not None:
            avg_meanproductgsd += meanproductgsd
            num_meanproductgsd += 1
        if meancollectedgsd is not None:
            avg_meancollectedgsd += meancollectedgsd
            num_meancollectedgsd += 1
            
        # orig_sizes[i] holds the original size of i-th image. After
        # we scale i-th image by i_th_image_pitch/0_th_image_pitch, we
        # store the new image box in placements[i].
        orig_sizes = []
        placements = []
        orig_sizes.append(ref_image_size)
        shiftx = 0
        shifty = int(round(tlc_pixel_lookup( ref_tlctime, ref_tlclist,
                                             ref_firstlinetime)))
        placement = BBox(shiftx, shifty,
                         shiftx + ref_image_size[0], shifty + ref_image_size[1])
        placements.append( placement )

        for filename in args[1:]:

            [tlctime, tlclist, firstlinetime, image_size, pitch, originx, pd,
             eph_list, att_list, llbox, htbox, ll_corners,
             meanproductgsd, meancollectedgsd] \
             = read_xml( xml_name(filename), options )
            all_ll_corners.append(ll_corners)

            # Sanity checks
            if ref_originx != originx:
                raise Exception('InputError', 'Must have the same originx in: '
                                + xml_name(args[0]) + ' ' + xml_name(filename) )
            if ref_pd != pd:
                raise Exception('InputError', 'Must have the same pd in: '
                                + xml_name(args[0]) + ' ' + xml_name(filename) )
            if ref_eph_list != eph_list:
                raise Exception('InputError', 'Must have the same ephem in: '
                                + xml_name(args[0]) + ' ' + xml_name(filename) )
            if ref_att_list != att_list:
                raise Exception('InputError', 'Must have the same att in: '
                                + xml_name(args[0]) + ' ' + xml_name(filename) )

            orig_sizes.append(image_size)
            pitch_ratio = pitch/ref_pitch
            scaled_image_size = [int(round(image_size[0]*pitch_ratio)),
                                 int(round(image_size[1]*pitch_ratio))]
            shiftx = 0
            shifty = int(round(tlc_pixel_lookup(ref_tlctime, ref_tlclist,
                                                firstlinetime)))
            placement = BBox(shiftx, shifty,
                             shiftx + scaled_image_size[0],
                             shifty + scaled_image_size[1])
            placements.append( placement )

            ref_llbox.expand_with_box(llbox)
            ref_htbox.expand_with_box(htbox)

            if meanproductgsd is not None:
                avg_meanproductgsd += meanproductgsd
                num_meanproductgsd += 1
            if meancollectedgsd is not None:
                avg_meancollectedgsd += meancollectedgsd
                num_meancollectedgsd += 1

        # Check that all input images have the same number of columns
        #for i in range(len(orig_sizes)):
        #    if orig_sizes[i][0] != orig_sizes[0][0]:
        #        raise Exception('InputError', 'Not all images have the same number of columns')
            
        if num_meanproductgsd != 0:
            avg_meanproductgsd   = avg_meanproductgsd/num_meanproductgsd
        if num_meancollectedgsd != 0:
            avg_meancollectedgsd = avg_meancollectedgsd/num_meancollectedgsd

        boundary = BBox()
        for placement in placements:
            boundary.expand_with_box(placement)

        #print "Output image size: %ix%i px" % (boundary.width(),boundary.height())
        for i in range(len(args)):
            placements[i].miny -= boundary.miny
            placements[i].maxy -= boundary.miny

        # Dummy check: Look for duplicate files that will insert at
        # same location. Files that do this would be the R?C1 variants
        # that have the same XML file due to download errors or
        # simplying mixing of R?C1 and the other format.
        seen = set()
        for i in range(len(args)):
            if placements[i].miny in seen:
                raise Exception('InputError', 'Input images have same input location which could be caused by repeated XML file or invalid TLC information')
            seen.add(placements[i].miny)

        scale = options.reduce_percent / 100.0
        suffix = ".r" + str(options.reduce_percent)

        # Write a composite scaled XML file. We first create the xml at
        # the original resolution and then we scale it.
        tree = ET.parse(xml_name(args[0]))
        root = tree.getroot()
        imd = root.find("IMD")
        imd.find("NUMROWS").text = str(boundary.height())
        imd.find("NUMCOLUMNS").text = str(boundary.width())
        for tlc_item in imd.find("IMAGE").find("TLCLISTList").findall("TLCLIST"):
            tokens = tlc_item.text.strip().split(' ')
            tokens[0] = str(int(round((float(tokens[0])))) - boundary.miny)
            tlc_item.text = " ".join(tokens)
        first_line_item = root.find("IMD").find("IMAGE").find("FIRSTLINETIME")
        tlc_lookup = tlc_time_lookup( ref_tlctime, ref_tlclist, boundary.miny )
        # The verbose operation below is needed for python 2.4
        first_line_item.text = tlc_lookup.strftime("%Y-%m-%dT%H:%M:%S") + '.' \
                               + format_microsec(str(tlc_lookup.microsecond)) + "Z"

        if not options.skip_rpc_gen:
            # Find the best-fitting RPC coefficients.
            temp_xml_file = tempfile.NamedTemporaryFile(suffix = ".xml", dir = ".");
            tree.write(temp_xml_file.name)
            rpc_cmd = "rpc_gen"
            rpc_args = ['--penalty-weight', str(options.penalty_weight),
                        '--lon-lat-height-box',
                        str(ref_llbox.minx), str(ref_llbox.miny),
                        str(ref_htbox.minx), str(ref_llbox.maxx),
                        str(ref_llbox.maxy), str(ref_htbox.maxx), temp_xml_file.name]
            rpc_model = run_and_parse_output( rpc_cmd, rpc_args, sep,
                                              options.verbose )
            try:
                # Set the RPC model info. Wipe all other outdated info.
                RPI = root.find("RPB").find("IMAGE")
                if RPI.find("ERRBIAS") != None: RPI.remove( RPI.find ("ERRBIAS") )
                if RPI.find("ERRRAND") != None: RPI.remove( RPI.find ("ERRRAND") )

                RPI.find("SAMPSCALE").text    = rpc_model["xy_scale"][0]
                RPI.find("LINESCALE").text    = rpc_model["xy_scale"][1]

                RPI.find("SAMPOFFSET").text   = rpc_model["xy_offset"][0]
                RPI.find("LINEOFFSET").text   = rpc_model["xy_offset"][1]

                RPI.find("LONGSCALE").text    = rpc_model["llh_scale"][0]
                RPI.find("LATSCALE").text     = rpc_model["llh_scale"][1]
                RPI.find("HEIGHTSCALE").text  = rpc_model["llh_scale"][2]

                RPI.find("LONGOFFSET").text   = rpc_model["llh_offset"][0]
                RPI.find("LATOFFSET").text    = rpc_model["llh_offset"][1]
                RPI.find("HEIGHTOFFSET").text = rpc_model["llh_offset"][2]

                RPI.find("LINENUMCOEFList").find("LINENUMCOEF").text = " ".join(rpc_model["line_num"])
                RPI.find("LINEDENCOEFList").find("LINEDENCOEF").text = " ".join(rpc_model["line_den"])
                RPI.find("SAMPNUMCOEFList").find("SAMPNUMCOEF").text = " ".join(rpc_model["samp_num"])
                RPI.find("SAMPDENCOEFList").find("SAMPDENCOEF").text = " ".join(rpc_model["samp_den"])
            except Exception, e:
                raise Exception('InputError', "File " + args[0] +
                                " lacks complete RPC model information.")
        else:
            if root.find("RPB") != None: root.remove( root.find ("RPB") )

        # Wipe other outdated info
        if root.find("TIL")   != None: root.remove( root.find("TIL") )

        # Ensure that the images are placed in order, from top to
        # bottom.  So the second image will partially cover the first
        # image, etc.  This is a bug fix for the situation when some
        # images have a black area at the bottom.
        (s_args, s_orig_sizes, s_placements, s_all_ll_corners) = \
                 sort_by_miny(args, orig_sizes, placements, all_ll_corners)

        print(s_args[0])

    except Usage, err:
        print >>sys.stderr, err.msg
        return 2

if __name__ == "__main__":
    sys.exit(main())
