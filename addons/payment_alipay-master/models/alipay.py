# -*- coding: utf-'8' "-*-"

import base64
try:
    import simplejson as json
except ImportError:
    import json
import logging
import urlparse
import werkzeug.urls
import urllib2
from urllib import urlencode, urlopen

try:
    import hashlib
    md5_constructor = hashlib.md5
    md5_hmac = md5_constructor
    sha_constructor = hashlib.sha1
    sha_hmac = sha_constructor
except ImportError:
    import md5
    md5_constructor = md5.new
    md5_hmac = md5
    import sha
    sha_constructor = sha.new
    sha_hmac = sha

md5 = md5_constructor

from openerp.addons.payment.models.payment_acquirer import ValidationError
from ..controllers.main import AlipayController
from openerp.osv import osv, fields
from openerp.tools.float_utils import float_compare
from openerp import SUPERUSER_ID

_logger = logging.getLogger(__name__)


def smart_str(s, encoding='utf-8', strings_only=False, errors='strict'):
    """
    Returns a bytestring version of 's', encoded as specified in 'encoding'.

    If strings_only is True, don't convert (some) non-string-like objects.
    """
    if strings_only and isinstance(s, (types.NoneType, int)):
        return s
    if not isinstance(s, basestring):
        try:
            return str(s)
        except UnicodeEncodeError:
            if isinstance(s, Exception):
                # An Exception subclass containing non-ASCII data that doesn't
                # know how to print itself properly. We shouldn't raise a
                # further exception.
                return ' '.join([smart_str(arg, encoding, strings_only,
                        errors) for arg in s])
            return unicode(s).encode(encoding, errors)
    elif isinstance(s, unicode):
        return s.encode(encoding, errors)
    elif s and encoding != 'utf-8':
        return s.decode('utf-8', errors).encode(encoding, errors)
    else:
        return s

def params_filter(params):
    ks = params.keys()
    ks.sort()
    newparams = {}
    prestr = ''
    for k in ks:
        v = params[k]
        k = smart_str(k, 'utf-8')
        if k not in ('sign','sign_type') and v != '':
            newparams[k] = smart_str(v, 'utf-8')
            prestr += '%s=%s&' % (k, newparams[k])
    prestr = prestr[:-1]
    return newparams, prestr

def build_mysign(prestr, key, sign_type = 'MD5'):
    if sign_type == 'MD5':
        return md5(prestr + key).hexdigest()
    return ''

class AcquirerAlipay(osv.Model):
    _inherit = 'payment.acquirer'

    def _get_alipay_urls(self, cr, uid, environment, context=None):
        """ Alipay URLS """
        if environment == 'prod':
            return {
                'alipay_url': 'https://mapi.alipay.com/gateway.do?'
            }
        else:
            return {
                'alipay_url': 'https://openapi.alipaydev.com/gateway.do?'
            }

    def _get_providers(self, cr, uid, context=None):
        providers = super(AcquirerAlipay, self)._get_providers(cr, uid, context=context)
        providers.append(['alipay', 'Alipay'])
        return providers

    _columns = {
        'alipay_partner_account': fields.char('Alipay Partner ID', required_if_provider='alipay'),
        'alipay_partner_key': fields.char('Alipay Partner Key', required_if_provider='alipay'),
        'alipay_seller_email': fields.char('Alipay Seller Email', required_if_provider='alipay'),

    }

    _defaults = {
        'fees_active': False,
        'fees_dom_fixed': 0.0,
        'fees_dom_var': 0.0,
        'fees_int_fixed': 0.0,
        'fees_int_var': 0.0,

    }

    def alipay_compute_fees(self, cr, uid, id, amount, currency_id, country_id, context=None):
        """ Compute alipay fees.

            :param float amount: the amount to pay
            :param integer country_id: an ID of a res.country, or None. This is
                                       the customer's country, to be compared to
                                       the acquirer company country.
            :return float fees: computed fees
        """
        acquirer = self.browse(cr, uid, id, context=context)
        if not acquirer.fees_active:
            return 0.0
        country = self.pool['res.country'].browse(cr, uid, country_id, context=context)
        if country and acquirer.company_id.country_id.id == country.id:
            percentage = acquirer.fees_dom_var
            fixed = acquirer.fees_dom_fixed
        else:
            percentage = acquirer.fees_int_var
            fixed = acquirer.fees_int_fixed
        fees = (percentage / 100.0 * amount + fixed ) / (1 - percentage / 100.0)
        return fees

    def alipay_form_generate_values(self, cr, uid, id, partner_values, tx_values, context=None):
        base_url = self.pool['ir.config_parameter'].get_param(cr, SUPERUSER_ID, 'web.base.url')
        acquirer = self.browse(cr, uid, id, context=context)

        alipay_tx_values = dict(tx_values)
        alipay_tx_values.update({
            'service': 'trade_create_by_buyer',
            'partner': acquirer.alipay_partner_account,
            'seller_email': acquirer.alipay_seller_email,
            '_input_charset': 'utf-8',
            'out_trade_no': tx_values['reference'],
            'subject': tx_values['reference'],
            'payment_type': '1',
            'logistics_type': 'EXPRESS',
            'logistics_fee': 0,
            'logistics_payment': 'SELLER_PAY',
            'price': tx_values['amount'],
            'quantity': 1,
            'body': '%s: %s' % (acquirer.company_id.name, tx_values['reference']),
            'return_url': '%s' % urlparse.urljoin(base_url, AlipayController._return_url),
            'notify_url': '%s' % urlparse.urljoin(base_url, AlipayController._notify_url),
        })

        params = alipay_tx_values
        params,prestr = params_filter(params)
        params['sign'] = build_mysign(prestr, acquirer.alipay_partner_key, 'MD5')
        params['sign_type'] = 'MD5'
        alipay_tx_values  = params

        return partner_values, alipay_tx_values

    def alipay_get_form_action_url(self, cr, uid, id, context=None):
        acquirer = self.browse(cr, uid, id, context=context)
        params = {
            '_input_charset': 'utf-8',
        }
        return self._get_alipay_urls(cr, uid, acquirer.environment, context=context)['alipay_url'] + urlencode(params)

class TxAlipay(osv.Model):
    _inherit = 'payment.transaction'

    _columns = {
        'alipay_txn_id': fields.char('Transaction ID'),
        'alipay_txn_type': fields.char('Transaction type'),
    }

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------

    def _alipay_form_get_tx_from_data(self, cr, uid, data, context=None):
        reference, txn_id = data.get('out_trade_no'), data.get('trade_no')
        if not reference or not txn_id:
            error_msg = 'Alipay: received data with missing reference (%s) or txn_id (%s)' % (reference, txn_id)
            _logger.error(error_msg)
            raise ValidationError(error_msg)

        # find tx -> @TDENOTE use txn_id ?
        tx_ids = self.pool['payment.transaction'].search(cr, uid, [('reference', '=', reference)], context=context)
        if not tx_ids or len(tx_ids) > 1:
            error_msg = 'Alipay: received data for reference %s' % (reference)
            if not tx_ids:
                error_msg += '; no order found'
            else:
                error_msg += '; multiple order found'
            _logger.error(error_msg)
            raise ValidationError(error_msg)
        return self.browse(cr, uid, tx_ids[0], context=context)

    def _alipay_form_validate(self, cr, uid, tx, data, context=None):
        status = data.get('trade_status')
        data = {
            'acquirer_reference': data.get('trade_no'),
            'alipay_txn_type': data.get('payment_type'),
            'partner_reference': data.get('buyer_id')
        }
        if status in ['WAIT_SELLER_SEND_GOODS']:
            _logger.info('Validated Alipay payment for tx %s: set as done' % (tx.reference))
            data.update(state='done', date_validate=data.get('gmt_payment', fields.datetime.now()))
            return tx.write(data)
        elif status in ['WAIT_BUYER_PAY']:
            _logger.info('Received notification for Alipay payment %s: set as pending' % (tx.reference))
            data.update(state='pending', state_message=data.get('pending_reason', ''))
            return tx.write(data)
        else:
            error = 'Received unrecognized status for Alipay payment %s: %s, set as error' % (tx.reference, status)
            _logger.info(error)
            data.update(state='error', state_message=error)
            return tx.write(data)

