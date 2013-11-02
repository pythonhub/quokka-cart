# coding: utf-8
import logging
from werkzeug.utils import import_string
from flask import session, current_app
from flask.ext.babel import lazy_gettext as _l
from quokka.utils import get_current_user, lazy_str_setting
from quokka.core.db import db
from quokka.core.models import Publishable, Ordered, Dated, Content


logger = logging.getLogger()


class Product(Content):
    unity_value = db.FloatField(default=0)
    weight = db.FloatField(default=0)
    dimensions = db.StringField()
    extra_value = db.FloatField(default=0)

    def get_title(self):
        return getattr(self, 'title', None)

    def get_description(self):
        return getattr(self, 'description', None)

    def get_unity_value(self):
        return getattr(self, 'unity_value', None)

    def get_weight(self):
        return getattr(self, 'weight', None)

    def get_dimensions(self):
        return getattr(self, 'dimensions', None)

    def get_extra_value(self):
        return getattr(self, 'extra_value', None)

    def get_uid(self):
        return str(self.id)

    meta = {
        'allow_inheritance': True
    }


class Item(Ordered, Dated, db.EmbeddedDocument):
    product = db.ReferenceField(Product)
    uid = db.StringField()
    title = db.StringField(required=True, max_length=255)
    description = db.StringField(required=True)
    link = db.StringField()
    quantity = db.FloatField(default=1)
    unity_value = db.FloatField(required=True, default=0)
    total_value = db.FloatField()
    weight = db.FloatField(default=0)
    dimensions = db.StringField()
    extra_value = db.FloatField(default=0)

    def get_uid(self):
        try:
            return self.product.get_uid()
        except:
            return self.uid

    @property
    def total(self):
        try:
            product = self.product
            self.title = self.title or product.get_title()
            self.description = self.description or product.get_description()
            self.link = self.link or product.get_absolute_url()
            self.unity_value = self.unity_value or product.get_unity_value()
            self.weight = self.weight or product.get_weight()
            self.dimensions = self.dimensions or product.get_dimensions()
            self.extra_value = self.extra_value or product.get_extra_value()
            self.uid = self.uid or product.get_uid()
        except:
            logger.info("There is no product or error occurred")

        self.total_value = (self.unity_value *
                            self.quantity) + self.extra_value

        return self.total_value


class Payment(db.EmbeddedDocument):
    uid = db.StringField()
    payment_system = db.StringField()
    method = db.StringField()
    value = db.FloatField()
    extra_value = db.FloatField()
    date = db.DateTimeField()
    confirmed_at = db.DateTimeField()
    status = db.StringField()


class Processor(Publishable, db.DynamicDocument):
    identifier = db.StringField(max_length=100, unique=True)
    module = db.StringField()
    requires = db.ListField(db.StringField())
    description = db.StringField()
    title = db.StringField()
    image = db.ReferenceField('Image')
    link = db.StringField()
    config = db.DictField(default=lambda: {})

    def import_processor(self):
        return import_string(self.module)

    def get_instance(self, *args, **kwargs):
        if not 'config' in kwargs:
            kwargs['config'] = self.config
        return self.import_processor()(*args, **kwargs)

    def clean(self, *args, **kwargs):
        for item in (self.requires or []):
            import_string(item)
        super(Processor, self).clean(*args, **kwargs)

    def __unicode__(self):
        return self.identifier

    @classmethod
    def get_default_processor(cls):
        default = lazy_str_setting(
            'CART_DEFAULT_PROCESSOR',
            default={
                'module': 'quokka.modules.cart.processors.Dummy',
                'identifier': 'dummy'
            }
        )

        try:
            return cls.objects.get(identifier=default['identifier'])
        except:
            return cls.objects.create(**default)

    def save(self, *args, **kwargs):
        self.import_processor()
        super(Processor, self).save(*args, **kwargs)


class Cart(Publishable, db.DynamicDocument):
    STATUS = (
        ("pending", _l("Pending")),
        ("checked_out", _l("Checked out")),
        ("confirmed", _l("Confirmed")),
        ("cancelled", _l("Cancelled")),
        ("abandoned", _l("Abandoned")),
    )
    reference = db.GenericReferenceField()
    """reference must implement set_status(**kwargs) method
    arguments: status(str), value(float), date, uid(str), msg(str)
    and extra(dict).
    Also reference must implement get_uid() which will return
    the unique identifier for this transaction"""

    belongs_to = db.ReferenceField('User', default=get_current_user)
    items = db.ListField(db.EmbeddedDocumentField(Item))
    payment = db.ListField(db.EmbeddedDocumentField(Payment))
    status = db.StringField(choices=STATUS, default='pending')
    total = db.FloatField(default=0)
    extra_costs = db.DictField(default=lambda: {})
    sender_data = db.DictField(default=lambda: {})
    shipping_data = db.DictField(default=lambda: {})
    shippping_cost = db.FloatField(default=0)
    processor = db.ReferenceField(Processor,
                                  required=True,
                                  default=Processor.get_default_processor)
    checkout_code = db.StringField()  # The UID for transaction

    @property
    def uid(self):
        return self.get_uid()

    def get_uid(self):
        try:
            return self.reference.get_uid()
        except:
            return str(self.id)

    def __unicode__(self):
        return u"{o.uid} - {o.processor.identifier}".format(o=self)

    def get_extra_costs(self):
        if self.extra_costs:
            return sum(self.extra_costs.values())

    @classmethod
    def get_cart(cls):
        """
        create a new cart related to the session
        if there is a current logged in user it will be set
        else it will be set during the checkout.
        """
        session.permanent = current_app.config.get(
            "CART_PERMANENT_SESSION", True)
        try:
            cart = cls.objects.get(id=session.get('cart_id'))
        except Exception:
            cart = cls(status="pending")
            cart.save()
            session['cart_id'] = str(cart.id)
        return cart

    def assign(self):
        self.belongs_to = self.belongs_to or get_current_user()

    def save(self, *args, **kwargs):
        self.total = sum([item.total for item in self.items])
        self.assign()
        super(Cart, self).save(*args, **kwargs)

    def add_item(self, **kwargs):
        uid = kwargs.get('uid')

        if not uid:
            logger.warning("Cannot add item without an uid %s" % kwargs)
            return

        # check if item already exist and only update its values

        item = Item(**kwargs)
        self.items.append(item)
        self.save()

    def checkout(self, processor=None, *args, **kwargs):
        self.set_processor(processor)
        processor_instance = self.processor.get_instance(self, *args, **kwargs)
        if processor_instance.validate():
            return processor_instance.process()
        else:
            raise Exception("Cart did not validate")  # todo: specialize this

    def set_processor(self, processor):
        if not self.processor:
            self.processor = Processor.get_default_processor()

        if not processor:
            return

        if isinstance(processor, Processor):
            self.processor = processor
            return

        try:
            self.processor = Processor.objects.get(id=processor)
        except:
            self.processor = Processor.objects.get(identifier=processor)

        self.save()

    def remove_item(self, uid):
        pass

    def cancel(self):
        self.status = 'cancelled'
