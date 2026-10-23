import unittest

from actions import resolve_delivery_station_name


class StationNameResolutionTests(unittest.TestCase):
    def setUp(self):
        self.stations = [
            {'id': '16', 'code': 'CC', 'name': '长春站', 'enabled': True, 'allows_delivery': True},
            {'id': '21', 'code': 'BJS', 'name': '北京站', 'enabled': True, 'allows_delivery': True},
            {'id': '22', 'code': 'BJN', 'name': '北京南站', 'enabled': True, 'allows_delivery': True},
            {'id': '30', 'code': 'DISABLED', 'name': '北京西站', 'enabled': False, 'allows_delivery': True},
            {'id': '31', 'code': 'NO_DELIVERY', 'name': '北京北站', 'enabled': True, 'allows_delivery': False},
        ]

    def test_exact_unique_station_name_resolves_without_exposing_id_to_user(self):
        station, matches = resolve_delivery_station_name(' 北京站 ', self.stations)
        self.assertEqual(station['id'], '21')
        self.assertEqual(matches, [])

    def test_unique_station_prefix_resolves_but_city_prefix_with_multiple_stations_is_ambiguous(self):
        station, matches = resolve_delivery_station_name('长春', self.stations)
        self.assertEqual(station['name'], '长春站')
        station, matches = resolve_delivery_station_name('北京', self.stations)
        self.assertIsNone(station)
        self.assertEqual({item['name'] for item in matches}, {'北京站', '北京南站'})

    def test_disabled_or_non_delivery_station_is_never_selected(self):
        station, matches = resolve_delivery_station_name('北京西站', self.stations)
        self.assertIsNone(station)
        self.assertEqual(matches, [])
        station, matches = resolve_delivery_station_name('北京北站', self.stations)
        self.assertIsNone(station)
        self.assertEqual(matches, [])

    def test_unknown_name_does_not_guess(self):
        station, matches = resolve_delivery_station_name('上海站', self.stations)
        self.assertIsNone(station)
        self.assertEqual(matches, [])


if __name__ == '__main__':
    unittest.main()
