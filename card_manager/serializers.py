from rest_framework import serializers
from .models import User, Collection


# User serializer
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'username',
            'password',
            'discord_id',
            'collections'
        ]


# Card serializer
class CardSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'card_name',
            'scryfall_id',
            'tcg_id',
            'set',
            'collector_number',
            'finish',
            'print_uri',
            'collection',
            'price',
            'quantity'
        ]
