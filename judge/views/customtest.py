import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils.functional import cached_property
from django.views.generic.base import View, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.translation import gettext_lazy as _

from judge.forms import ProblemCustomTestForm
from judge.models import Language
from judge.models.runtime import CustomTestHistory
from judge.utils.views import TitleMixin
from judge.views.run_code import call_judge0, check_rate_limit

logger = logging.getLogger(__name__)


class CustomTestView(LoginRequiredMixin, TitleMixin, TemplateView):
    template_name = "customtest.html"

    def get_content_title(self):
        return _("Custom Test")

    def get_title(self):
        return _("Custom Test")

    @cached_property
    def default_language(self):
        return self.request.profile.language

    def get_context_data(self, **kwargs):
        last_run = CustomTestHistory.objects.filter(user=self.request.user).order_by('-id').first()

        context = super().get_context_data(**kwargs)

        if last_run:
            context["default_lang"] = last_run.language
            context["form"] = ProblemCustomTestForm(initial={
                "language": last_run.language,
                "source": last_run.code,
                "input": last_run.input_data
            })
        else:
            context["default_lang"] = self.default_language
            context["form"] = ProblemCustomTestForm(initial={"language": self.default_language})

        context["langs"] = Language.objects.all()
        context["ACE_URL"] = settings.ACE_URL

        return context


class CustomTestRunView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if not check_rate_limit(request.user.id):
            return JsonResponse({"error": "Too many requests. Please try again later."}, status=429)

        form = ProblemCustomTestForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors}, status=400)

        data = form.cleaned_data
        language = data.get("language")

        CustomTestHistory.objects.update_or_create(
            user=request.user,
            defaults={
                'language': language,
                'code': data.get("source", ""),
                'input_data': data.get("input", ""),
            }
        )

        try:
            result = call_judge0(data.get("source", ""), language, data.get("input", ""))
            return JsonResponse(result)
        except Exception as e:
            logger.error("Error calling Judge0 API: %s", e)
            return JsonResponse({"error": "Execution failed. Please try again."}, status=500)
