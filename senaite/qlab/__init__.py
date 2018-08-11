# -*- coding: utf-8 -*-

import logging
from bika.lims.browser import BrowserView

logger = logging.getLogger("senaite.qlab")


class ExampleBrowserView(BrowserView):

    def example(self):
        """
        """
        logger.info('Inside example')
        return 'Done: ExampleBrowserView called successfully'
