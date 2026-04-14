from django.test import SimpleTestCase, override_settings

from judge.utils.infinite_paginator import infinite_paginate


class InfinitePaginatorTestCase(SimpleTestCase):
    def test_first_page(self):
        self.assertEqual(infinite_paginate(range(1, 101), 1, 10, 2).object_list, list(range(1, 11)))

        self.assertEqual(infinite_paginate(range(1, 101), 1, 10, 2).page_range, [1, 2, 3, 4, 5, False, 10])
        self.assertEqual(infinite_paginate(range(1, 31), 1, 10, 2).page_range, [1, 2, 3])
        self.assertEqual(infinite_paginate(range(1, 22), 1, 10, 2).page_range, [1, 2, 3])
        self.assertEqual(infinite_paginate(range(1, 21), 1, 10, 2).page_range, [1, 2])
        self.assertEqual(infinite_paginate(range(1, 12), 1, 10, 2).page_range, [1, 2])
        self.assertEqual(infinite_paginate(range(1, 11), 1, 10, 2).page_range, [1])
        self.assertEqual(infinite_paginate(range(1, 2), 1, 10, 2).page_range, [1])
        self.assertEqual(infinite_paginate([], 1, 10, 2).page_range, [1])

    def test_gaps(self):
        self.assertEqual(infinite_paginate(range(1, 101), 1, 10, 2).page_range, [1, 2, 3, 4, 5, False, 10])
        self.assertEqual(infinite_paginate(range(1, 101), 2, 10, 2).page_range, [1, 2, 3, 4, 5, False, 10])
        self.assertEqual(infinite_paginate(range(1, 101), 3, 10, 2).page_range, [1, 2, 3, 4, 5, False, 10])
        self.assertEqual(infinite_paginate(range(1, 101), 5, 10, 2).page_range, [1, False, 4, 5, 6, False, 10])
        self.assertEqual(infinite_paginate(range(1, 101), 6, 10, 2).page_range, [1, False, 5, 6, 7, False, 10])

    def test_end(self):
        self.assertEqual(infinite_paginate(range(1, 101), 7, 10, 2).page_range, [1, False, 6, 7, 8, False, 10])
        self.assertEqual(infinite_paginate(range(1, 101), 8, 10, 2).page_range, [1, False, 6, 7, 8, 9, 10])
        self.assertEqual(infinite_paginate(range(1, 101), 9, 10, 2).page_range, [1, False, 6, 7, 8, 9, 10])
        self.assertEqual(infinite_paginate(range(1, 101), 10, 10, 2).page_range, [1, False, 6, 7, 8, 9, 10])
        self.assertEqual(infinite_paginate(range(1, 100), 10, 10, 2).page_range, [1, False, 6, 7, 8, 9, 10])
        self.assertEqual(infinite_paginate(range(1, 100), 10, 10, 2).object_list, list(range(91, 100)))

    @override_settings(VNOJ_LOW_POWER_MODE=True)
    def test_low_power_unknown_end(self):
        self.assertEqual(infinite_paginate(range(1, 101), 1, 10, 2).page_range, [1, 2, False])
        self.assertEqual(infinite_paginate(range(1, 101), 5, 10, 2).page_range, [1, False, 4, 5, 6, False])
        self.assertEqual(infinite_paginate(range(1, 101), 10, 10, 2).page_range, [1, False, 9, 10, 11, False])
