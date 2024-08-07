from ..repositories.collection_repository import CollectionRepository
from ..repositories.card_repository import CardRepository
from ..repositories.user_repository import UserRepository
from decimal import Decimal
import requests
import json
import time
import csv
import logging
from rest_framework import status

logger = logging.getLogger('card_manager')


class CollectionService:
    def __init__(self):
        self.collection_repository = CollectionRepository()
        self.card_repository = CardRepository()
        self.user_repository = UserRepository()

    # Create a collection
    def create_collection(self, user, collection_name):
        try:
            collection = self.collection_repository.create_collection(user, collection_name)
            return {'message': 'Collection created successfully'}, status.HTTP_201_CREATED
        except Exception as e:
            logger.error(f"Error creating collection: {e}")
            return {'error': 'An error occurred'}, status.HTTP_500_INTERNAL_SERVER_ERROR

    # Get cards from a collection
    def get_cards(self, collection):
        cards = collection.cards.all()
        card_list = []
        total_value = Decimal(0.00)
        total_quantity = 0
        for card in cards:
            card_list.append({
                'card_name': card.card_name,
                'scryfall_id': card.scryfall_id,
                'tcg_id': card.tcg_id,
                'set': card.set,
                'collector_number': card.collector_number,
                'finish': card.finish,
                'print_uri': card.print_uri,
                'price': card.price,
                'quantity': card.quantity
            })
            total_value += card.price * card.quantity
            total_quantity += card.quantity
        card_list.insert(0, {"card_count": total_quantity, "total_value": total_value})
        return card_list

    # Get collection details for a user
    def get_collection_by_name(self, user, collection_name):
        collection = self.collection_repository.get_collection_by_name(user, collection_name)
        return self.get_cards(collection)

    # Get all collections for a user
    def get_all_collections(self, user):
        collections = self.collection_repository.get_all_collections_by_user(user)
        collection_list = []
        for collection in collections:
            collection_list.append({
                'collection_name': collection.collection_name,
                'cards': self.get_cards(collection)
            })
        return collection_list

    # Clear all cards from a user's collection
    def clear_collection(self, user, collection_name):
        try:
            collection = self.collection_repository.clear_collection(user, collection_name)
            return {'message': 'Collection cleared successfully'}, status.HTTP_200_OK
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            return {'error': 'An error occurred'}, status.HTTP_500_INTERNAL_SERVER_ERROR

    # Parse CSV and get card info from scryfall
    def process_csv(self, csv_file):
        try:
            url = "https://api.scryfall.com/cards/collection"
            headers = {"Content-Type": "application/json"}
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            reader = csv.DictReader(decoded_file)
            card_list = [row for row in reader]
            logger.info(f"{len(card_list)} unique cards found")
            identifiers, scryfall_data = [], []
            finish_map = {}
            count, total_quantity, total_sent = 0, 0, 0

            for card in card_list:
                backup_url = "https://api.scryfall.com/cards/"
                total_quantity += int(card['Quantity'])
                collector_number = card.get('Card Number') or card.get('Collector number')
                set_code = card.get('Set Code') or card.get('Set code')

                if 'Product ID' in card:
                    backup_url += f"tcgplayer/{card['Product ID']}"
                elif 'Scryfall ID' in card:
                    backup_url += f"{card['Scryfall ID']}"

                finish = None
                if 'Foil' in card:
                    finish = card['Foil'].lower()
                elif 'Printing' in card:
                    finish = card['Printing'].lower()
                if finish == 'normal':
                    finish = 'nonfoil'

                finish_map[f"{set_code}-{collector_number}".upper()] = {
                    'backup_url': backup_url,
                    'finish': finish,
                    'quantity': card['Quantity']
                }

                identifiers.append({
                    'collector_number': collector_number,
                    'set': set_code
                })
                count += 1
                if len(identifiers) == 75 or count == len(card_list):
                    logger.info(f"Identifiers length: {len(identifiers)}")
                    total_sent += len(identifiers)
                    body = {"identifiers": identifiers}
                    # TODO: Make async requests to improve performance
                    response = requests.post(url, headers=headers, data=json.dumps(body))
                    if response.status_code == 200:
                        data = response.json()
                        for not_found in data.get('not_found'):
                            card_key = f'{not_found["set"]}-{not_found["collector_number"]}'.upper()
                            backup_url = finish_map[card_key]['backup_url']
                            backup_response = requests.get(backup_url)
                            if backup_response.status_code == 200:
                                backup_data = backup_response.json()
                                new_key = f"{backup_data.get('set')}-{backup_data.get('collector_number')}".upper()
                                finish_map[new_key] = {
                                    'finish': finish_map[card_key]['finish'],
                                    'quantity': finish_map[card_key]['quantity']
                                }
                                data['data'].append(backup_data)
                            else:
                                logger.error(f"Error fetching card details: {backup_response.json()}")
                        scryfall_data.append(data)
                    else:
                        logger.error(f"Error fetching card details: {response.json()}")
                    identifiers = []
            return scryfall_data, finish_map

        except Exception as e:
            logger.error(f"Error processing file: {e}")
            return None, None

    # Add cards to collection
    def add_collection(self, user, collection_name, scryfall_data, finish_map):
        try:
            collection = self.collection_repository.get_collection_by_name(user, collection_name)
            error_count = 0
            all_cards = []
            for data in scryfall_data:
                for selected_card in data.get('data'):
                    name = selected_card.get('name')
                    scryfall_id = selected_card.get('id')
                    tcgplayer_id = selected_card.get('tcgplayer_id') or 0
                    set_name = selected_card.get('set_name')
                    set_code = selected_card.get('set')
                    collector_number = selected_card.get('collector_number')
                    uri = selected_card.get('uri')

                    key = f"{selected_card.get('set')}-{collector_number}".upper()
                    finish = finish_map[key]['finish']
                    quantity = finish_map[key]['quantity']

                    found_finish = None
                    for finish_option in selected_card.get('finishes'):
                        if finish_option == finish:
                            found_finish = finish
                            break
                        elif finish_option == 'etched' and finish == 'foil':
                            found_finish = 'etched'
                            break
                    if found_finish is None:
                        logger.error(f"Finish not found for {name} - {set_name} - {collector_number}")
                        error_count += 1
                        continue

                    price = None
                    if finish == 'nonfoil':
                        price = selected_card.get('prices').get('usd')
                    elif finish == 'foil':
                        price = selected_card.get('prices').get('usd_foil')
                    elif finish == 'etched':
                        price = selected_card.get('prices').get('usd_etched')

                    if price is None:
                        price = Decimal(0.00)

                    card_data = {
                        'card_name': name,
                        'scryfall_id': scryfall_id,
                        'tcg_id': tcgplayer_id,
                        'set': set_name,
                        'set_code': set_code,
                        'collector_number': collector_number,
                        'finish': finish,
                        'print_uri': uri,
                        'collection': collection,
                        'price': price,
                        'quantity': quantity
                    }
                    all_cards.append(card_data)
                    # TODO: Look for option do add card_manager to database in bulk
                    # self.card_repository.create_card(card_data)
            self.card_repository.create_cards(all_cards, collection)
            logger.info(f"Error count: {error_count}")
            return {'message': 'Data received successfully'}, status.HTTP_200_OK
        except Exception as e:
            logger.error(f"Error updating collection: {e}")
            return {'error': 'An error occurred'}, status.HTTP_500_INTERNAL_SERVER_ERROR

        # Delelete a collection
    def delete_collection(self, user, collection_name):
        try:
            collection = self.collection_repository.delete_collection(user, collection_name)
            return {'message': 'Collection cleared successfully'}, status.HTTP_200_OK
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            return {'error': 'An error occurred'}, status.HTTP_500_INTERNAL_SERVER_ERROR