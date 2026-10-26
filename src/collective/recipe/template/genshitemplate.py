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
        # Genshi builds the search path for {% include %} out of "filepath",
        # so a template that came from a URL must keep passing None here:
        # with the URL, every include would resolve against a directory that
        # does not exist and render as nothing. Without it, an include
        # resolves against the buildout directory.
        template = NewTextTemplate(
            self.source,
            filepath=self.input, filename=self.input)
        context_params = {'parts': self.buildout, 'options': self.options}
        if 'eggs' in self.options:
            log.info('Making working set out of the eggs')
            eggs = Eggs(self.buildout, self.options['recipe'], self.options)
            __, eggs = eggs.working_set()
            context_params['eggs'] = eggs
        context = Context(**context_params)
        try:
            self.result = template.generate(
                context).render(encoding='utf-8')
        except UndefinedError as e:
            # A URL template has no "input", so name the URL instead.
            raise zc.buildout.UserError(
                'Error in template %s:\n%s' % (self.input or self.url, e.msg))
        self.result = self.result.decode('utf-8')
