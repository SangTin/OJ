import collections.abc
import inspect
from math import ceil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.paginator import EmptyPage, InvalidPage
from django.http import Http404, HttpResponseRedirect
from django.utils.functional import cached_property
from django.utils.inspect import method_has_no_args


class InfinitePage(collections.abc.Sequence):
    """
    A page of a paginator that supports infinite pagination.

    This paginator won't count all the items in the queryset, helpful for large data
    like submissions, problems, etc.

    In low power mode, the paginator will assume there's a next page if the current page is full.
    This eliminates the need to count the next pages items.
    """
    def __init__(self, object_list, number, unfiltered_queryset, page_size, pad_pages, paginator):
        self.object_list = list(object_list)
        self.number = number
        self.unfiltered_queryset = unfiltered_queryset
        self.page_size = page_size
        self.pad_pages = pad_pages
        self.num_pages = 1e3000
        self.paginator = paginator

    def __repr__(self):
        return '<Page %s of many>' % self.number

    def __len__(self):
        return len(self.object_list)

    def __getitem__(self, index):
        return self.object_list[index]

    @cached_property
    def _after_up_to_pad(self):
        first_after = self.number * self.page_size
        padding_length = self.pad_pages * self.page_size
        queryset = self.unfiltered_queryset[first_after:first_after + padding_length + 1]
        c = getattr(queryset, 'count', None)
        if callable(c) and not inspect.isbuiltin(c) and method_has_no_args(c):
            return c()
        return len(queryset)

    def has_next(self):
        if settings.VNOJ_LOW_POWER_MODE:
            # Optimized: assume there's a next page if current page is full
            # Trade-off: will show next button on last page when total items is multiple of page_size
            return len(self.object_list) >= self.page_size
        return self._after_up_to_pad > 0

    def has_previous(self):
        return self.number > 1

    def has_other_pages(self):
        return self.has_previous() or self.has_next()

    def next_page_number(self):
        if not self.has_next():
            raise EmptyPage()
        return self.number + 1

    def previous_page_number(self):
        if self.number <= 1:
            raise EmptyPage()
        return self.number - 1

    def start_index(self):
        return (self.page_size * (self.number - 1)) + 1

    def end_index(self):
        return self.start_index() + len(self.object_list)

    @cached_property
    def trailing_page(self):
        if settings.VNOJ_LOW_POWER_MODE:
            if self.has_next():
                return None
            return self.number

        trailing_pages = int(ceil(self._after_up_to_pad / self.page_size))
        if trailing_pages <= self.pad_pages:
            return self.number + trailing_pages
        return None

    @cached_property
    def page_range(self):
        last_page = self.trailing_page
        if last_page is not None and last_page <= 7:
            return list(range(1, last_page + 1))

        result = [1]

        def append_page(page):
            if page > 1 and (not result or result[-1] != page):
                result.append(page)

        def append_gap():
            if result[-1] is not False:
                result.append(False)

        right_edge = self.number + 1 if last_page is None else last_page - 1

        if self.number <= 4:
            end = min(5, right_edge)
            for page in range(2, end + 1):
                append_page(page)
        elif last_page is not None and self.number >= last_page - 3:
            append_gap()
            for page in range(max(2, last_page - 4), last_page):
                append_page(page)
        else:
            append_gap()
            end = self.number + 1
            for page in range(self.number - 1, end + 1):
                append_page(page)

        if last_page is None:
            append_gap()
        else:
            if result[-1] + 1 < last_page:
                append_gap()
            append_page(last_page)

        return result


class DummyPaginator:
    is_infinite = True

    def __init__(self, per_page):
        self.per_page = per_page


class InvalidPageJump(Exception):
    def __init__(self, fallback_url):
        self.fallback_url = fallback_url


def infinite_paginate(queryset, page, page_size, pad_pages, paginator=None):
    if page < 1:
        raise EmptyPage()
    sliced = queryset[(page - 1) * page_size:page * page_size]
    if page > 1 and not sliced:
        raise EmptyPage()
    return InfinitePage(sliced, page, queryset, page_size, pad_pages, paginator)


class InfinitePaginationMixin:
    pad_pages = 2

    @property
    def use_infinite_pagination(self):
        return True

    def get_pad_pages(self):
        if settings.VNOJ_LOW_POWER_MODE:
            return 1
        return self.pad_pages

    def get_invalid_page_jump(self):
        fallback_url = self.request.GET.get('fallback_url')
        if not self.use_infinite_pagination or not fallback_url:
            return None

        scheme, netloc, path, query, fragment = urlsplit(fallback_url)
        if scheme or netloc or not path.startswith('/'):
            return None

        params = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key != 'page_jump_invalid']
        params.append(('page_jump_invalid', '1'))
        return urlunsplit(('', '', path, urlencode(params), fragment))

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except InvalidPageJump as e:
            return HttpResponseRedirect(e.fallback_url)

    def paginate_queryset(self, queryset, page_size):
        if not self.use_infinite_pagination:
            paginator, page, object_list, has_other = super().paginate_queryset(queryset, page_size)
            paginator.is_infinite = False
            return paginator, page, object_list, has_other

        page_kwarg = self.page_kwarg
        page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
        try:
            page_number = int(page)
        except ValueError:
            raise Http404('Page cannot be converted to an int.')
        try:
            paginator = DummyPaginator(page_size)
            page = infinite_paginate(queryset, page_number, page_size, self.get_pad_pages(), paginator)
            return paginator, page, page.object_list, page.has_other_pages()
        except InvalidPage as e:
            fallback_url = self.get_invalid_page_jump()
            if fallback_url is not None:
                raise InvalidPageJump(fallback_url)
            raise Http404('Invalid page (%(page_number)s): %(message)s' % {
                'page_number': page_number,
                'message': str(e),
            })
