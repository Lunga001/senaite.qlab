import App
import os
import tempfile
import traceback
from DateTime import DateTime
from bika.lims import bikaMessageFactory as _
from bika.lims import logger
from bika.lims.browser import BrowserView
from bika.lims.browser.analysisrequest.publish import \
    AnalysisRequestPublishView as ARPV
from bika.lims.browser.analysisrequest.publish import \
    AnalysisRequestDigester  # as ARD
from bika.lims.idserver import renameAfterCreation
from bika.lims.utils import encode_header
from bika.lims.utils import to_utf8
from bika.lims.utils import attachPdf, createPdf
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.Utils import formataddr
from smtplib import SMTPAuthenticationError
from smtplib import SMTPRecipientsRefused, SMTPServerDisconnected
from plone.app.content.browser.interfaces import IFolderContentsView
from plone.resource.utils import queryResourceDirectory
from Products.CMFCore.WorkflowCore import WorkflowException
from Products.CMFPlone.utils import _createObjectByType
from Products.CMFCore.utils import getToolByName
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from zope.interface import implements
from senaite.qlab.vocabularies import getARReportTemplates


class AnalysisRequestPublishView(ARPV):
    implements(IFolderContentsView)

    def __init__(self, context, request, publish=False):
        BrowserView.__init__(self, context, request)
        self.context = context
        self.request = request
        self._publish = publish
        self._ars = [self.context]
        self._digester = AnalysisRequestDigester()

    def __call__(self):
        if self.context.portal_type == 'AnalysisRequest':
            self._ars = [self.context]
        elif self.context.portal_type in ('AnalysisRequestsFolder', 'Client') \
                and self.request.get('items', ''):
            uids = self.request.get('items').split(',')
            uc = getToolByName(self.context, 'uid_catalog')
            self._ars = [obj.getObject() for obj in uc(UID=uids)]
        else:
            # Do nothing
            self.destination_url = self.request.get_header(
                "referer", self.context.absolute_url())

        # Group ARs by client
        groups = {}
        for ar in self._ars:
            idclient = ar.aq_parent.id
            if idclient not in groups:
                groups[idclient] = [ar]
            else:
                groups[idclient].append(ar)
        self._arsbyclient = [group for group in groups.values()]

        # Report may want to print current date
        self.current_date = self.ulocalized_time(DateTime(), long_format=True)

        # Do publish?
        if self.request.form.get('publish', '0') == '1':
            self.publishFromPOST()
        else:
            return self.template()

    def getAvailableFormats(self):
        """Returns the available formats found in templates/reports
        """
        return getARReportTemplates()

    def _renderTemplate(self):
        """Returns the html template to be rendered in accordance with the
        template specified in the request ('template' parameter)
        """
        templates_dir = 'templates/reports'
        embedt = self.request.form.get('template', self._DEFAULT_TEMPLATE)
        if embedt.find(':') >= 0:
            prefix, template = embedt.split(':')
            templates_dir = queryResourceDirectory('reports', prefix).directory
            embedt = template
        embed = ViewPageTemplateFile(os.path.join(templates_dir, embedt))
        return embedt, embed(self)

    def getReportTemplate(self):
        """Returns the html template for the current ar and moves to
        the next ar to be processed. Uses the selected template
        specified in the request ('template' parameter)
        """
        embedt = ""
        try:
            embedt, reptemplate = self._renderTemplate()
        except:
            tbex = traceback.format_exc()
            arid = self._ars[self._current_ar_index].id
            reptemplate = \
                "<div class='error-report'>%s - %s '%s':<pre>%s</pre></div>" \
                % (arid, _("Unable to load the template"), embedt, tbex)
        self._nextAnalysisRequest()
        return reptemplate

    def getReportStyle(self):
        """Returns the css style to be used for the current template.
        If the selected template is 'default.pt', this method will
        return the content from 'default.css'. If no css file found
        for the current template, returns empty string
        """
        template = self.request.form.get('template', self._DEFAULT_TEMPLATE)
        content = ''
        if template.find(':') >= 0:
            prefix, template = template.split(':')
            resource = queryResourceDirectory('reports', prefix)
            css = '{0}.css'.format(template[:-3])
            if css in resource.listDirectory():
                content = resource.readFile(css)
        else:
            this_dir = os.path.dirname(os.path.abspath(__file__))
            templates_dir = os.path.join(this_dir, 'templates/reports/')
            path = '%s/%s.css' % (templates_dir, template[:-3])
            with open(path, 'r') as content_file:
                content = content_file.read()
        return content

    def publishFromHTML(self, aruid, results_html):
        # The AR can be published only and only if allowed
        uc = getToolByName(self.context, 'uid_catalog')
        ars = uc(UID=aruid)
        if not ars or len(ars) != 1:
            return []

        ar = ars[0].getObject()
        wf = getToolByName(self.context, 'portal_workflow')
        allowed_states = ['verified', 'published']
        # Publish/Republish allowed?
        if wf.getInfoFor(ar, 'review_state') not in allowed_states:
            # Pre-publish allowed?
            if not ar.getAnalyses(review_state=allowed_states):
                return []

        # HTML written to debug file
        debug_mode = App.config.getConfiguration().debug_mode
        if debug_mode:
            tmp_fn = tempfile.mktemp(suffix=".html")
            logger.debug("Writing HTML for %s to %s" % (ar.Title(), tmp_fn))
            open(tmp_fn, "wb").write(results_html)

        # Create the pdf report (will always be attached to the AR)
        # we must supply the file ourself so that createPdf leaves it alone.
        pdf_fn = tempfile.mktemp(suffix=".pdf")

        # PDF written to debug file
        if debug_mode:
            logger.debug("Writing PDF for %s to %s" % (ar.Title(), pdf_fn))
        else:
            os.remove(pdf_fn)

        recipients = []
        contact = ar.getContact()
        lab = ar.bika_setup.laboratory
        if contact:
            recipients = [{
                'UID': contact.UID(),
                'Username': to_utf8(contact.getUsername()),
                'Fullname': to_utf8(contact.getFullname()),
                'EmailAddress': to_utf8(contact.getEmailAddress()),
                'PublicationModes': contact.getPublicationPreference()
            }]
        reportid = ar.generateUniqueId('ARReport')
        report = _createObjectByType("ARReport", ar, reportid)
        report.edit(
            AnalysisRequest=ar.UID(),
        )
        report.unmarkCreationFlag()
        renameAfterCreation(report)
        fn = report.getId()
        reports_link = "<a href='{}'>{}</a>".format(ar.absolute_url(), fn)
        coa_nr_text = 'COA ID is generated on publication'
        results_html = results_html.replace(coa_nr_text, reports_link)
        # Create the pdf report for the supplied HTML.
        pdf_report = createPdf(results_html, False)
        report.edit(
            Pdf=pdf_report,
            Recipients=recipients
        )

        fld = report.getField('Pdf')
        fld.get(report).setFilename(fn + ".pdf")
        fld.get(report).setContentType('application/pdf')

        # Set status to prepublished/published/republished
        status = wf.getInfoFor(ar, 'review_state')
        transitions = {'verified': 'publish',
                       'published': 'republish'}
        transition = transitions.get(status, 'prepublish')
        try:
            wf.doActionFor(ar, transition)
        except WorkflowException:
            pass

        # compose and send email.
        # The managers of the departments for which the current AR has
        # at least one AS must receive always the pdf report by email.
        # https://github.com/bikalabs/Bika-LIMS/issues/1028
        mime_msg = MIMEMultipart('related')
        mime_msg['Subject'] = self.get_mail_subject(ar)[0]
        mime_msg['From'] = formataddr(
            (encode_header(lab.getName()), lab.getEmailAddress()))
        mime_msg.preamble = 'This is a multi-part MIME message.'
        msg_txt = MIMEText(results_html, _subtype='html')
        mime_msg.attach(msg_txt)

        to = []
        mngrs = ar.getResponsible()
        for mngrid in mngrs['ids']:
            name = mngrs['dict'][mngrid].get('name', '')
            email = mngrs['dict'][mngrid].get('email', '')
            if email:
                to.append(formataddr((encode_header(name), email)))

        if len(to) > 0:
            # Send the email to the managers
            mime_msg['To'] = ','.join(to)
            attachPdf(mime_msg, pdf_report, ar.id)

            try:
                host = getToolByName(self.context, 'MailHost')
                host.send(mime_msg.as_string(), immediate=True)
            except SMTPServerDisconnected as msg:
                logger.warn("SMTPServerDisconnected: %s." % msg)
            except SMTPRecipientsRefused as msg:
                raise WorkflowException(str(msg))
            except SMTPAuthenticationError as msg:
                logger.warn("SMTPAuthenticationFailed: %s." % msg)

        # Send report to recipients
        recips = self.get_recipients(ar)
        for recip in recips:
            if 'email' not in recip.get('pubpref', []) \
                    or not recip.get('email', ''):
                continue

            title = encode_header(recip.get('title', ''))
            email = recip.get('email')
            formatted = formataddr((title, email))

            # Create the new mime_msg object, cause the previous one
            # has the pdf already attached
            mime_msg = MIMEMultipart('related')
            mime_msg['Subject'] = self.get_mail_subject(ar)[0]
            mime_msg['From'] = formataddr(
                (encode_header(lab.getName()), lab.getEmailAddress()))
            mime_msg.preamble = 'This is a multi-part MIME message.'
            msg_txt = MIMEText(results_html, _subtype='html')
            mime_msg.attach(msg_txt)
            mime_msg['To'] = formatted

            # Attach the pdf to the email if requested
            if pdf_report and 'pdf' in recip.get('pubpref'):
                attachPdf(mime_msg, pdf_report, ar.id)

            # For now, I will simply ignore mail send under test.
            if hasattr(self.portal, 'robotframework'):
                continue

            msg_string = mime_msg.as_string()

            # content of outgoing email written to debug file
            if debug_mode:
                tmp_fn = tempfile.mktemp(suffix=".email")
                logger.debug(
                    "Writing MIME message for %s to %s" % (ar.Title(), tmp_fn))
                open(tmp_fn, "wb").write(msg_string)

            try:
                host = getToolByName(self.context, 'MailHost')
                host.send(msg_string, immediate=True)
            except SMTPServerDisconnected as msg:
                logger.warn("SMTPServerDisconnected: %s." % msg)
            except SMTPRecipientsRefused as msg:
                raise WorkflowException(str(msg))
            except SMTPAuthenticationError as msg:
                logger.warn("SMTPAuthenticationFailed: %s." % msg)

        return [ar]
