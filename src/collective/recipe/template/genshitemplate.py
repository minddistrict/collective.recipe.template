import logging
import sys
import zc.buildout
from collective.recipe.template import Recipe as Base
from zc.recipe.egg.egg import Eggs


log = logging.getLogger(__name__)


class Recipe(Base):

    def _execute(self):
        from genshi.template import Context, NewTextTemplate
        from genshi.template.eval import UndefinedError
        template = NewTextTemplate(
            self.source,
            filepath=self.input, filename=self.input)
        context_params = {'parts': self.buildout, 'options': self.options}
        if 'eggs' in context:
            log.info('Making working set out of the eggs')
            eggs = Eggs(self.buildout, self.options['recipe'], self.options)
            __, eggs = eggs.working_set()
            context_params['eggs'] = eggs
        context = Context(**context_params)
        try:
            self.result = template.generate(
                context).render(encoding='utf-8')
        except UndefinedError as e:
            raise zc.buildout.UserError(
                'Error in template %s:\n%s' % (self.input, e.msg))
        self.result = self.result.decode('utf-8')
