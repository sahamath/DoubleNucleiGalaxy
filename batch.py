import os
import sys
import numpy as np
import pandas as pd
import logging as log
import datetime as dt
import matplotlib.pyplot as plt
import concurrent.futures as conf
from importlib import reload
from textwrap import TextWrapper as txwr
from PIL import Image

import galaxy
galaxy = reload(galaxy)
Galaxy = galaxy.Galaxy

# Create the /Logs folder for the root directory if it doesn't already exist
if not os.path.isdir("Logs") :
    os.mkdir("Logs")

def dateFmt () :
    """Returns the date component of the run log file"""
    dtStr = str(dt.datetime.now())
    dtStr = dtStr[:dtStr.find('.')]
    dtStr = dtStr.replace(' ', '_')
    return dtStr

# Set the logger for this run of classifications
runlog = log.getLogger(__name__)
runlog.setLevel(log.INFO)
runLogPath = "Logs/run_{}.log".format(dateFmt())
fileHandler = log.FileHandler(runLogPath)
fileHandler.setFormatter(log.Formatter("%(levelname)s : RUN_INIT : %(asctime)s : %(message)s",
                         datefmt='%m/%d/%Y %I:%M:%S %p'))

# Ensures that there is only one fileHandler for the current logger
for h in runlog.handlers :
    runlog.removeHandler(h)

runlog.addHandler(fileHandler)
runlog.info("Batch runner started!")

sys.setrecursionlimit(10**6)

class Batch () :
    """
    Class that loads the FITS data corresponding to a .csv file of SDSS objIDs
    and performs DAGN classification on them
    """

    batchRoot = "Batches"

    def getBatch (batchName, bands="ri", rad=40, csv=None) :
        """ Class method to get a batch """

        try :
            batch = Batch(batchName, (batchName + ".csv") if csv is None else csv,
                    bands, rad)
        except (FileNotFoundError, ValueError) as e :
            print("Error initialising batch!")
            print("Kindly check the latest message in the logfile '{}' for a fix.".format(
                os.path.join(os.getcwd(), runLogPath)
            ))
            print("Abort!")
            batch = None
        finally :
            return batch

    def logFixFmt (fix, k=50) :
        """ Formats error messages for the run logger """
        return  2*(k*"#" + '\n') + txwr(width=k).fill(text=fix) + '\n' + 2*(k*"#" + '\n')

    #############################################################################################################
    #############################################################################################################

    def __prelude__ (self) :
        """
        Sets up files and folders and checks for existence of
        folder indicated by attribute batchName and the 'csv' filename
        """

        # To access fileHandler of the logger
        global fileHandler
        fileHandler.setFormatter(log.Formatter("%(levelname)s : RUN_INIT : %(asctime)s : %(message)s",
                             datefmt='%m/%d/%Y %I:%M:%S %p'))

        # Checks if the batchRoot directory has been created at the root directory
        runlog.info("Checking environment for the new batch.")
        if not os.path.isdir(Batch.batchRoot) :
            runlog.critical("Data folder not found!\n\n{}".format(Batch.logFixFmt(
                "Please create a folder named 'Data' in the notebook directory and rerun!"
            )))
            raise FileNotFoundError

        # Checks if batchName folder exists in batchRoot
        if not os.path.isdir(self.batchFold) :
            runlog.critical("Batch folder not found\n\n{}".format(Batch.logFixFmt(
                "Please create a folder for the batch at '{}' and rerun!".format(self.batchFold)
            )))
            raise FileNotFoundError

        ######################################################################
        # Checks if the .csv file exists. If the 'csv' argument is None, the
        # name of the .csv file is taken to be the same name as its containing
        # folder
        ######################################################################
        if not os.path.exists(self.csvPath) :
            runlog.critical("Batch .csv file at path '{}' not found\n\n{}".format(self.batchFold, Batch.logFixFmt(
                "Please supply the name of the appropriate .csv file and rerun!"
            )))
            raise FileNotFoundError

        ######################################################################
        # Changing name of the run log fileHandler to reflect the batch it is
        # presently handling
        ######################################################################
        runlog.info("Valid environment! Changing log format to handle batch '{}'".format(self.batchName))
        fileHandler.setFormatter(log.Formatter("%(levelname)s : {} : %(asctime)s : %(message)s".format(self.batchName),
                         datefmt='%m/%d/%Y %I:%M:%S %p'))

        # Ensures only one fileHandler exists
        for h in runlog.handlers :
            runlog.removeHandler(h)
        runlog.addHandler(fileHandler)

        ######################################################################
        # Creates a /FITS folder in the batch folder where all the FITS files will
        # be stored
        ######################################################################
        if not os.path.exists(self.fitsFold) :
            os.mkdir(self.fitsFold)
            runlog.info("Created FITS folder for batch")
        else :
            runlog.info("FITS folder for the batch already exists")

    def __setBatchLogger__ (self) :
        """
        Sets a logger to record the results as a .csv file
        Can do because logging module of Python is thread-safe
        """

        # Creating the .csv file for results
        writeHeader = False if os.path.exists(self.resPath) else True
        reslog = log.getLogger(self.batchName + "_result")
        reslog.setLevel(log.INFO)
        resFH = log.FileHandler(self.resPath)
        resFH.setFormatter(log.Formatter("%(message)s"))

        # Ensuring only one file handler exists
        for h in reslog.handlers :
            reslog.removeHandler(h)
        reslog.addHandler(resFH)

        if writeHeader :
            reslog.info("objID,r-peaks,i-peaks,verdict")
            runlog.info("Created result csv")
        else :
            runlog.info("Result csv already exists")

        self.reslog = reslog

    def __setClassList__ (self) :
        """ Sets the list of galaxies to classify -
            1. Reads the main .csv file
            2. Reads the .csv file which contains results of
            already classified galaxies
            3. The set difference of (1) and (2) are the galaxies
            yet to be classified
        """

        # try block to read the master .csv file
        try :
            df = pd.read_csv(self.csvPath, dtype=object, usecols=["objID", "ra", "dec"])
        except ValueError as e :
            runlog.critical("Invalid columns in .csv file\n\n{}".format(Batch.logFixFmt(
                "Please ensure columns 'objID', 'ra' and 'dec' are present in the .csv \
                file (in that order) and rerun!"
                )))
            raise e

        # try block to read the result .csv file
        try :
            resIDs = [] if not os.path.exists(self.resPath) else\
                    list(pd.read_csv(self.resPath, dtype=object)['objID'])
        except ValueError as e :
            runlog.critical("Error in loading result csv file\n\n{}".format(Batch.logFixFmt(
                "Please ensure the first column in 'objID'. If the file is corrupted, delete \
                it and rerun!"
            )))

        self.galaxies = [Galaxy(str(objid), (ra, dec), self.fitsFold, self.bands)
                        for objid, ra, dec
                        in zip(df["objID"], df["ra"], df["dec"])
                        if str(objid) not in resIDs]

    def __init__ (self, batchName, csvName, bands="ri", rad=40) :
        """
        Constructor for the batch. Does the following -
            1. Sets up the folders/environment for the batch
            2. Sets the result logger for the batch in the batch folder
            3. Reads in the batch .csv and the result .csv file and decides
            which objects remain to be classified
        """

        self.batchName = batchName
        self.csvName = csvName
        self.__prelude__()
        runlog.info("Successfully created environment for batch")

        # Function to check if the band(s) supplied by the user is valid
        areBandsValid = lambda bs : len([b for b in bs if b in "ugriz"]) == len(bs) != 0

        ######################################################################
        # If the bands are not valid, a warning is logged
        # This is because the Galaxy object internally takes care of
        # invalid bands
        ######################################################################
        if not areBandsValid(bands) :
            runlog.warning("One or more bands in '{}' invalid\n\n{}".format(bands, Batch.logFixFmt(
            "Please ensure that bands are a combination of 'ugriz' only!"
            )))
            raise ValueError("Invalid Band. Please use 'ugriz'")

        self.bands = bands

        # Sets the result logger for the batch
        self.__setBatchLogger__()

        # Initialises the galaxy objects that are yet to be classified in this batch
        self.__setClassList__()

        print("Batch successfully initialised. \
        \nThe classifications will be available at {} \
        \nIn the event of any program crash/error, please check the log file at {} for details"\
        .format(self.resPath, os.path.join(os.getcwd(), runLogPath)))
        print("Number of galaxies to classify - {}".format(len(self.galaxies)))

    def __str__ (self) :
        """ Batch object to string """
        return self.csvPath

    def __len__ (self) :
        """ Length of the batch """
        return len(self.galaxies)

    @property
    def batchFold (self) :
        """ Property attribute - Path of the batch folder """
        return os.path.join (os.getcwd(), Batch.batchRoot, self.batchName)

    @property
    def fitsFold (self) :
        """ Property attribute - Path of the FITS folder for the batch """
        return os.path.join (self.batchFold, "FITS")

    @property
    def resFold (self) :
        """ Property attribute - Path of the folder that contains result images
        post classificaton """
        return os.path.join(self.batchFold, "Results")

    @property
    def csvPath (self) :
        """ Property attribute - Path of the csv File """
        return os.path.join(self.batchFold, self.csvName)

    @property
    def resPath (self) :
        """ Property attribute - Path of the result .csv file """
        return os.path.join(self.batchFold, self.csvName[:-4] + "_result.csv")

    @property
    def logPath (self) :
        """ Property attribute - Path of the log file for the batch """
        return os.path.join(os.getcwd(), self.batchFold, "{}.log".format(self.batchName))

    def classifyGal (self, gal) :
        """ Performs the entire pipeline of operations for a single galaxy """

        gal.download()
        runlog.info("{} --> Downloaded".format(gal.objid))
        gal.cutout()
        runlog.info("{} --> Loaded and done cutout".format(gal.objid))
        gal.smoothen()
        runlog.info("{} --> Smoothed".format(gal.objid))
        gal.hullRegion()
        runlog.info("{} --> Found hull region".format(gal.objid))
        gal.distInfo()
        runlog.info("{} --> Comptued intensity distribution".format(gal.objid))
        gal.filter()
        runlog.info("{} --> Filtered".format(gal.objid))
        gal.fitGaussian()
        runlog.info("{} --> Fit gaussian".format(gal.objid))
        gal.cutoffNoise()
        runlog.info("{} --> Computed noise/signal cutoff".format(gal.objid))
        gal.shc()
        runlog.info("{} --> Performed stochastic hill climbing".format(gal.objid))
        gal.verdict()

        return gal.getVerdLine(), gal.getCsvLine()

    def classifyBatch (self) :
        """
        For each galaxy in the batch's list, performs the following (multithreaded) -
            1. Load the FITS file and do the cutout
            2. Smoothen raw cutout data
            3. Find the hull region where peak searching is done
            4. Computes the intensity distribution in the hull region
            5. Filters which galaxies are worth classifying or not based on
            the above data. The steps below are only applied to objects that pass
            through this filtration
            6. Fits a gaussian to the intensity distribution
            7. Computes the noise/signal cutoff
            8. Performs stochastic gradient ascent to find a set of raw peaks
            9. Performs the final classification based on connected components
        """

        print ("Classifying the batch of {} galaxies".format(len(self.galaxies)))
        with conf.ThreadPoolExecutor() as exec :
            verds = [exec.submit(self.classifyGal, g) for g in self.galaxies]

            for i, f in enumerate(conf.as_completed(verds)) :
                verdline, csvline = f.result()
                self.reslog.info(csvline)
                print("{}. {}".format(i, verdline))

        print ("Classification done!".format(len(self.galaxies)))

    def genResults (self) :
        """
        Generates the result for every galaxy in the batch -
        Plots the smoothed image with hull boundary and peaks, if any
        """

        resPath = os.path.join(self.batchFold, "Results")
        if not os.path.isdir(resPath) :
            os.mkdir(resPath)

        runlog.info("Generating results for currently monitoring batch")
        for i, g in enumerate(self.galaxies) :
            for b in g.bands :
                if b not in "ugirz" :
                    continue

                img = g.getFinPeaksMarked(b, True)
                plt.imshow(img)
                plt.axis('off')
                plt.savefig(os.path.join(resPath, "{}-{}_result.png".format(g.objid, b)),
                            bbox_inches='tight',
                            pad_inches=0)
                plt.close()

            runlog.info("{}. {} --> Generated plot".format(i+1, g.objid))

        runlog.info("Generated results for currently monitoring batch")
        print("Results generated for the batch. Please check the contents of {}".format(self.resFold))

    #############################################################################################################
    #############################################################################################################

    def procDiagnose(self, constrictHist=False, invHist=False) :
        """
        Generates the following for filtered galaxies only -
            1. Hull with signal
            2. Scatter plot of intensity distribution and gaussian fit
        """

        diagPath = os.path.join(self.batchFold, "Proc-Diag")
        if not os.path.isdir(diagPath) :
            os.mkdir(diagPath)

        runlog.info("Diagnosing currently monitoring batch")
        for i, g in enumerate(self.galaxies) :
            for b, filt in g.filtrate.items() :
                if filt :
                    continue

                # Hull with signal
                img = g.getHullMarked(b, True)
                svimg = Image.fromarray(img.astype(np.uint8))
                svimg.save(os.path.join(diagPath, "{}-{}_hullSignal.png".format(g.objid, b)))

                # Scatter and fit
                argPack = g.getGaussPlot(b)
                for i in [0, 1, 2] :
                    plt.plot(*argPack[i]["args"], **argPack[i]["kwargs"])
                plt.savefig(os.path.join(diagPath, "{}-{}_gaussFit.png".format(g.objid, b)))
                plt.close()

            runlog.info("{}. {} --> Diagnosed".format(i+1, g.objid))

        runlog.info("Diagnosed currently monitoring batch")
