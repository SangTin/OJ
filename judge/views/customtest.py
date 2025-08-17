import base64
from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property
from django.views.generic.base import View, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.utils.translation import gettext_lazy as _

import judge0
from judge.forms import ProblemCustomTestForm
from judge.models import Language
from judge.models.runtime import CustomTestHistory
from judge.utils.views import TitleMixin

# TODO: once this is working, remove the UserPassesTestMixin
class CustomTestView(LoginRequiredMixin, UserPassesTestMixin, TitleMixin, TemplateView):
    raise_exception = True
    template_name = "customtest.html"

    def test_func(self):
        return self.request.user.has_perm('judge.test_site')

    def handle_no_permission(self):
        from django.template.response import TemplateResponse
        return TemplateResponse(
            self.request,
            'coming_soon.html',
            context={
                'title': _("Custom Test"),
                'message': _('This feature is coming soon! Stay tuned for updates.')
            },
            status=403
        )
    
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
    def call_judge0(self, source_code, input_data, language):
        try:
            safe_source_code = base64.b64encode(source_code.encode("utf-8")).decode("utf-8")
            safe_input_data = base64.b64encode(input_data.encode("utf-8")).decode("utf-8") if input_data else ""

            client = judge0.Client(
                endpoint=settings.JUDGE0_API_URL,
                auth_headers={"X-Auth-Token": "your_token"},
            )
            response = judge0.run(
                client=client,
                source_code=safe_source_code,
                language=language.judge0.id,
                stdin=safe_input_data,
            )
            return response
        except Exception as e:
            print("Error occurred while calling Judge0 API:", e)
            return None

    def post(self, request, *args, **kwargs):
        form = ProblemCustomTestForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": "Invalid input"}, status=400)

        data = form.cleaned_data
        language = data.get("language")
        
        #Save to history
        CustomTestHistory.objects.update_or_create(
            user=request.user,
            language=language,
            code=data.get("source", ""),
            input_data=data.get("input", "")
        )

        api_response = self.call_judge0(
            data.get("source", ""),
            data.get("input", ""),
            language,
        )
        if not api_response:
            return JsonResponse({"error": "Judge0 call failed"}, status=500)

        compile_output = api_response.compile_output
        output = api_response.stdout if api_response.stdout else _("Standard output is empty")
        if compile_output:
            output = compile_output
        return JsonResponse({"output": output})