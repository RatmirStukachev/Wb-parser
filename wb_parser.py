import requests
import urllib.parse
import csv
import time
import sys
from typing import Optional, Dict, Any, List

class WildberriesParser:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Origin': 'https://www.wildberries.ru',
            'Referer': 'https://www.wildberries.ru/',
        })
        # Try finding the correct main menu
        self.menu_data: List[Dict[str, Any]] = []

    def load_menu(self):
        """Скачивает структуру меню Wildberries для поиска ID категорий."""
        url = "https://static-basket-01.wbbasket.ru/vol0/data/main-menu-ru-ru-v2.json"
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            self.menu_data = response.json()
            print("Меню категорий успешно загружено.")
        except Exception as e:
            print(f"Ошибка при загрузке меню категорий: {e}")
            sys.exit(1)

    def find_category_info(self, target_url: str, current_menu: List[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Ищет параметры категории в меню по её ссылке."""
        if current_menu is None:
            current_menu = self.menu_data

        # Убираем домен, если он передан
        parsed_url = urllib.parse.urlparse(target_url)
        path = parsed_url.path
        if path.endswith('/') and len(path) > 1:
            path = path[:-1]

        for item in current_menu:
            item_url = item.get('url', '')
            if item_url == path:
                return item

            if 'childs' in item:
                result = self.find_category_info(path, item['childs'])
                if result:
                    return result
        return None

    def parse_category(self, url: str) -> List[Dict[str, Any]]:
        """Парсит все товары из переданной категории."""
        if not self.menu_data:
            self.load_menu()

        category_info = self.find_category_info(url)
        if not category_info:
            print(f"Не удалось найти категорию с URL: {url}")
            return []

        shard = category_info.get('shard')
        query = category_info.get('query')

        if not shard or not query:
            print(f"Категория найдена, но не содержит необходимых параметров для API (shard/query).")
            return []

        print(f"Найдена категория: {category_info.get('name')}")
        print(f"Сбор данных (shard: {shard}, query: {query})...")

        all_products = []
        page = 1
        max_pages = 100 # WB API обычно отдает не больше 100 страниц (ограничение в 100 * 100 = 10000 товаров)

        while page <= max_pages:
            print(f"Обработка страницы {page}...", end="\r")

            # Формируем URL для API
            # Актуальный домен API может меняться (catalog.wb.ru или search.wb.ru). Используем универсальный catalog.wb.ru/catalog/{shard}/v2/catalog
            # Но сейчас WB использует search.wb.ru/exactmatch/ru/common/v5/search или catalog.wb.ru/catalog/{shard}/v2/catalog
            # Попробуем catalog.wb.ru/catalog/{shard}/v2/catalog
            # Для надежности используем API v2:
            # https://catalog.wb.ru/catalog/{shard}/v2/catalog?{query}&page={page}&dest=-1257786

            api_url = f"https://catalog.wb.ru/catalog/{shard}/v4/catalog"
            params = {
                "ab_testing": "false",
                "appType": 1,
                "curr": "rub",
                "dest": -1257786, # Стандартный dest
                "page": page,
                "spp": 30,
            }

            # Парсим query и добавляем в params
            query_params = urllib.parse.parse_qs(query)
            for k, v in query_params.items():
                params[k] = v[0]

            response = None
            try:
                response = self.session.get(api_url, params=params, timeout=10)

                # WB может возвращать 204 No Content, когда страницы заканчиваются
                if response.status_code == 204:
                    break

                response.raise_for_status()
                data = response.json()

                products = data.get('data', {}).get('products', []) if 'data' in data else data.get('products', [])
                if not products:
                    # Если товары пустые, значит страницы закончились
                    break

                for p in products:
                    # Разбираем данные товара
                    item_id = p.get('id')
                    sizes = p.get('sizes', [])

                    # Цены лежат в sizes[0]['price'] (если есть)
                    price_discount = None
                    price_basic = None
                    if sizes and 'price' in sizes[0]:
                        price_info = sizes[0]['price']
                        # Цены в API WB обычно умножены на 100 (копейки)
                        price_discount = price_info.get('total', 0) / 100 if 'total' in price_info else None
                        price_basic = price_info.get('basic', 0) / 100 if 'basic' in price_info else None
                    else:
                        # Sometimes price is directly in 'priceU' or 'salePriceU' in the root or 'extended' block
                        price_discount = p.get('salePriceU', 0) / 100 if 'salePriceU' in p else None
                        price_basic = p.get('priceU', 0) / 100 if 'priceU' in p else None

                    product_data = {
                        'ID': item_id,
                        'Название': p.get('name', ''),
                        'Бренд': p.get('brand', ''),
                        'Цена со скидкой': price_discount,
                        'Цена без скидки': price_basic,
                        'Рейтинг': p.get('reviewRating', 0),
                        'Количество отзывов': p.get('feedbacks', 0),
                        'Ссылка': f"https://www.wildberries.ru/catalog/{item_id}/detail.aspx"
                    }
                    all_products.append(product_data)

                page += 1
                time.sleep(0.5) # Небольшая пауза, чтобы не забанили

            except requests.exceptions.RequestException as e:
                # If we hit 429 Too Many Requests, wait and retry
                if response is not None and response.status_code == 429:
                    print(f"\nСлишком много запросов (429). Ждем 10 секунд и повторяем попытку страницы {page}...")
                    time.sleep(10)
                    continue
                else:
                    print(f"\nОшибка сети на странице {page}: {e}")
                    time.sleep(2)
                    break
            except Exception as e:
                print(f"\nНепредвиденная ошибка на странице {page}: {e}")
                break

        print(f"\nСбор завершен. Найдено товаров: {len(all_products)}")
        return all_products

    def save_to_csv(self, products: List[Dict[str, Any]], filename: str = "products.csv"):
        """Сохраняет список товаров в CSV файл."""
        if not products:
            print("Нет данных для сохранения.")
            return

        fieldnames = ['ID', 'Название', 'Бренд', 'Цена со скидкой', 'Цена без скидки', 'Рейтинг', 'Количество отзывов', 'Ссылка']

        try:
            # Используем utf-8-sig для Excel
            with open(filename, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                writer.writeheader()
                for p in products:
                    writer.writerow(p)
            print(f"Данные успешно сохранены в файл: {filename}")
        except Exception as e:
            print(f"Ошибка при сохранении в CSV: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python wb_parser.py <url_категории>")
        print("Пример: python wb_parser.py https://www.wildberries.ru/catalog/elektronika/smartfony-i-telefony/vse-smartfony")
        sys.exit(1)

    url = sys.argv[1]
    parser = WildberriesParser()
    products = parser.parse_category(url)
    parser.save_to_csv(products)
