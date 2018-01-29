# -*- coding: utf-8 -*-

import logging
import imghdr
import os
from bika.lims.browser import BrowserView
from bika.lims.catalog import CATALOG_ANALYSIS_REQUEST_LISTING
from bika.lims.utils import tmpID
from bika.lims import api
from Products.CMFPlone.utils import _createObjectByType
from Products.CMFCore.utils import getToolByName
from zope.component import getUtility

logger = logging.getLogger("senaite.addon")

class ExampleBrowserView(BrowserView):

    def example(self):
        """ 
        """
        logger.info('Inside example')
        return 'Done: ExampleBrowserView called successfully'
