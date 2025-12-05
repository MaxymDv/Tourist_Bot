import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, \
    ConversationHandler
from googlemaps import Client as GoogleMapsClient

load_dotenv()
LOCATION, MOOD, SELECTING_PLACES, ROUTE = range(4)

MAX_LOCATIONS = 3  #місця з однієї категорії
ATTRACTIONS_BETWEEN_FOOD_DEFAULT = 2
RADIUS = 2000  # Радіус пошуку

api_key = os.getenv("GMAPS_API_KEY")
gmaps = GoogleMapsClient(key=api_key)

PLACE_TYPES = {
    'restaurants': {
        'emoji': '🍽️', 'name': 'Ресторани', 'types': ['restaurant', 'cafe'], 'category': 'food'
    },
    'cafes': {
        'emoji': '☕', 'name': 'Кав\'ярні', 'types': ['cafe', 'bakery'], 'category': 'food'
    },
    'attractions': {
        'emoji': '🏛️', 'name': 'Пам\'ятки', 'types': ['tourist_attraction', 'museum', 'point_of_interest'],
        'category': 'attraction'
    },
    'parks': {
        'emoji': '🌳', 'name': 'Парки', 'types': ['park'], 'category': 'attraction'
    },
    'shopping': {
        'emoji': '🛍️', 'name': 'Шопінг', 'types': ['shopping_mall', 'store'], 'category': 'attraction'
    },
    'entertainment': {
        'emoji': '🎭', 'name': 'Розваги', 'types': ['amusement_park', 'night_club', 'movie_theater'],
        'category': 'attraction'
    }
}


def get_google_walking_data(gmaps_client, start_loc, end_loc):
    try:
        origin = (start_loc['lat'], start_loc['lng'])
        destination = (end_loc['lat'], end_loc['lng'])

        matrix = gmaps_client.distance_matrix(origins=[origin], destinations=[destination], mode='walking')

        if matrix['status'] == 'OK':
            element = matrix['rows'][0]['elements'][0]
            if element['status'] == 'OK':
                return {
                    'distance_text': element['distance']['text'],
                    'distance_value': element['distance']['value'],  # метри
                    'duration_text': element['duration']['text'],
                    'duration_value': element['duration']['value']  # секунди
                }
    except Exception as e:
        print(f"Distance Matrix Error: {e}")

    # Заглушка на випадок помилки
    return {'distance_text': 'N/A', 'distance_value': 0, 'duration_text': 'N/A', 'duration_value': 0}


def get_nearby_places(gmaps_client, location, place_type, radius):
    "Отримує список місць поблизу через Places API"
    try:
        places_result = gmaps_client.places_nearby(
            location=location,
            radius=radius,
            type=place_type
        )

        places = []
        for place in places_result.get('results', [])[:MAX_LOCATIONS]:
            place_info = {
                'place_id': place['place_id'],
                'name': place['name'],
                'location': place['geometry']['location'],
                'rating': place.get('rating', 'N/A'),
                'user_ratings_total': place.get('user_ratings_total', 0),
                'vicinity': place.get('vicinity', ''),
                'types': place.get('types', []),
                'photos': place.get('photos', [])
            }
            places.append(place_info)

        return places
    except Exception as e:
        print(f"Error getting places: {e}")
        return []


def calculate_optimal_route(selected_places, start_location, mood, attractions_between_food):
    """
    Будує маршрут, обираючи наступну точку за найменшим чосом ХОДЬБИ (Distance Matrix).
    """
    if not selected_places:
        return []

    food_places = [p for p in selected_places if p['category'] == 'food']
    attraction_places = [p for p in selected_places if p['category'] == 'attraction']

    route = []
    current_location = start_location

    def find_nearest(candidates, current_loc):
        """Знаходить кандидата, до якого йти найшвидше"""
        if not candidates:
            return None

        best_candidate = None
        min_duration = float('inf')

        # Готуємо координати для пакетного запиту
        destinations = [(c['location']['lat'], c['location']['lng']) for c in candidates]
        origin = (current_loc['lat'], current_loc['lng'])

        try:
            # Один запит до Google для перевірки всіх кандидатів
            matrix = gmaps.distance_matrix(origins=[origin], destinations=destinations, mode='walking')
            elements = matrix['rows'][0]['elements']

            for i, element in enumerate(elements):
                if element['status'] == 'OK':
                    duration = element['duration']['value']
                    # Шукаємо мінімальний час
                    if duration < min_duration:
                        min_duration = duration
                        best_candidate = candidates[i]
        except Exception as e:
            print(f"Routing API Error: {e}")
            return candidates[0]  # Fallback: беремо першого

        return best_candidate

    #Алгоритм побудови
    if mood == 'hungry':
        # Спочатку їжа
        if food_places:
            closest = find_nearest(food_places, current_location)
            if closest:
                route.append(closest)
                food_places.remove(closest)
                current_location = closest['location']

        # Чергування
        while food_places or attraction_places:
            for _ in range(attractions_between_food):
                if attraction_places:
                    closest = find_nearest(attraction_places, current_location)
                    if closest:
                        route.append(closest)
                        attraction_places.remove(closest)
                        current_location = closest['location']
                else:
                    break

            if food_places and (not route or route[-1]['category'] != 'food'):
                closest = find_nearest(food_places, current_location)
                if closest:
                    route.append(closest)
                    food_places.remove(closest)
                    current_location = closest['location']

    else:  # mood == 'adventurous'
        if attraction_places:
            closest = find_nearest(attraction_places, current_location)
            if closest:
                route.append(closest)
                attraction_places.remove(closest)
                current_location = closest['location']

        while food_places or attraction_places:
            for _ in range(attractions_between_food):
                if attraction_places:
                    closest = find_nearest(attraction_places, current_location)
                    if closest:
                        route.append(closest)
                        attraction_places.remove(closest)
                        current_location = closest['location']
                else:
                    break

            if food_places and (not route or route[-1]['category'] != 'food'):
                closest = find_nearest(food_places, current_location)
                if closest:
                    route.append(closest)
                    food_places.remove(closest)
                    current_location = closest['location']

    return route


def calculate_total_route_info(route, start_location):
    """
    Проходить по готовому маршруту і запитує фінальні деталі шляху (Start -> A -> B -> End).
    Зберігає інформацію про перехід у поле 'step_info' кожного місця.
    Повертає загальну відстань (км) та час (хв).
    """
    if not route:
        return 0, 0

    total_km = 0
    total_minutes = 0

    #Від старту до першої точки
    data = get_google_walking_data(gmaps, start_location, route[0]['location'])
    total_km += data['distance_value'] / 1000
    total_minutes += data['duration_value'] / 60
    route[0]['step_info'] = f"🚶 {data['distance_text']} ({data['duration_text']}) від старту"

    #Між точками маршруту
    for i in range(len(route) - 1):
        data = get_google_walking_data(gmaps, route[i]['location'], route[i + 1]['location'])
        total_km += data['distance_value'] / 1000
        total_minutes += data['duration_value'] / 60
        # Записуємо інфо у НАСТУПНУ точку (скільки йти до неї від попередньої)
        route[i + 1]['step_info'] = f"🚶 {data['distance_text']} ({data['duration_text']})"

    return total_km, total_minutes


# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """
🌍 **Smart Tourist Bot** 🗺️

Я побудую для вас ідеальний піший маршрут, враховуючи реальний час ходьби!

✨ Що я вмію:
• 🔍 Знаходити найкращі місця
• ⏱️ Рахувати реальний час між точками
• 🍽️ Балансувати їжу та розваги

Поділіться локацією, щоб почати! 📍
    """
    keyboard = [[KeyboardButton("📍 Поділитися локацією", request_location=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    return LOCATION


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = update.message.location
    context.user_data['location'] = {
        'lat': location.latitude,
        'lng': location.longitude
    }
    mood_message = "📍 Локацію отримано!\nТепер розкажіть, як ваш настрій сьогодні? 😊"
    keyboard = [
        [InlineKeyboardButton("🍕 Чогось би поїсти...", callback_data="mood_hungry")],
        [InlineKeyboardButton("🚀 Готовий до пригод!", callback_data="mood_adventurous")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(mood_message, reply_markup=reply_markup)
    return MOOD


async def handle_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mood = query.data.replace('mood_', '')
    context.user_data['mood'] = mood

    mood_emoji = "🍕" if mood == "hungry" else "🚀"
    mood_text = "голодний" if mood == "hungry" else "готовий до пригод"

    await query.edit_message_text(
        f"{mood_emoji} Чудово! Ви {mood_text}!\n\n"
        f"🎯 Оберіть категорії місць, які хочете відвідати:"
    )

    keyboard = []
    for key, place_type in PLACE_TYPES.items():
        keyboard.append([InlineKeyboardButton(
            f"{place_type['emoji']} {place_type['name']}",
            callback_data=f"category_{key}"
        )])

    keyboard.append([InlineKeyboardButton("✅ Готово, будуємо маршрут!", callback_data="build_route")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_text(
        "Натискайте на категорії, щоб додати або видалити їх:",
        reply_markup=reply_markup
    )

    context.user_data['selected_categories'] = []
    return SELECTING_PLACES


async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "build_route":
        if not context.user_data.get('selected_categories'):
            await query.answer("⚠️ Оберіть хоча б одну категорію!", show_alert=True)
            return SELECTING_PLACES

        await query.edit_message_text("🤔 Аналізую карту, перевіряю затори та будую маршрут...")

        location = context.user_data['location']
        selected_categories = context.user_data['selected_categories']
        loc_tuple = (location['lat'], location['lng'])

        all_places = []

        try:
            # Збір місць
            for category in selected_categories:
                place_type_info = PLACE_TYPES[category]
                places = get_nearby_places(gmaps, loc_tuple, place_type_info['types'][0], RADIUS)

                for place_data in places:
                    place = {
                        'name': place_data['name'],
                        'category_name': place_type_info['name'],
                        'category': place_type_info['category'],
                        'emoji': place_type_info['emoji'],
                        'location': place_data['location'],
                        'rating': place_data['rating'],
                        'vicinity': place_data['vicinity']
                    }
                    all_places.append(place)

            if not all_places:
                await query.edit_message_text("😔 На жаль, не вдалося знайти місця поблизу.")
                return ConversationHandler.END

            # Сортування за рейтингом та ліміт
            all_places.sort(key=lambda x: x.get('rating') if x.get('rating') != 'N/A' else 0, reverse=True)
            if len(all_places) > 8:  # Трохи зменшив ліміт, щоб не перевантажувати API
                all_places = all_places[:8]

            # Побудова маршруту (з Distance Matrix)
            mood = context.user_data['mood']
            route = calculate_optimal_route(
                all_places,
                location,
                mood,
                attractions_between_food=ATTRACTIONS_BETWEEN_FOOD_DEFAULT
            )

            context.user_data['route'] = route
            await show_route(query.message, context)
            return ROUTE

        except Exception as e:
            print(f"Error in build_route: {e}")
            await query.message.reply_text("Сталася помилка при побудові. Спробуйте пізніше.")
            return ConversationHandler.END

    # Логіка вибору кнопок
    category = query.data.replace('category_', '')
    selected = context.user_data.get('selected_categories', [])

    if category in selected:
        selected.remove(category)
    else:
        selected.append(category)

    context.user_data['selected_categories'] = selected

    # Оновлення клавіатури
    keyboard = []
    for key, place_type in PLACE_TYPES.items():
        checkmark = "✅ " if key in selected else ""
        keyboard.append([InlineKeyboardButton(
            f"{checkmark}{place_type['emoji']} {place_type['name']}",
            callback_data=f"category_{key}"
        )])

    keyboard.append([InlineKeyboardButton("🗺️ Готово, будуємо маршрут!", callback_data="build_route")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_reply_markup(reply_markup=reply_markup)
    except:
        pass

    return SELECTING_PLACES


async def show_route(message, context: ContextTypes.DEFAULT_TYPE):
    route = context.user_data.get('route', [])
    location = context.user_data['location']
    mood = context.user_data['mood']

    if not route:
        await message.reply_text("😔 Маршрут вийшов порожнім.")
        return

    mood_text = "голодного туриста" if mood == "hungry" else "шукача пригод"

    # Підрахунок фінальних даних (тут оновлюється поле step_info)
    total_dist, total_min = calculate_total_route_info(route, location)

    route_text = f"""
🗺️ **Ваш Smart-маршрут ({mood_text}):**

📏 Всього йти: {total_dist:.2f} км
⏱️ Чистий час ходьби: ~{int(total_min)} хв
"""

    for i, place in enumerate(route, 1):
        step_info = place.get('step_info', '...')

        route_text += f"""
{i}. {place['emoji']} **{place['name']}**
   🏷️ {place['category_name']}
   📍 {place['vicinity']}
   ⭐ {place['rating']} | {step_info}
"""

    # Генеруємо посилання на Google Maps
    origin = f"{location['lat']},{location['lng']}"
    destination = f"{route[-1]['location']['lat']},{route[-1]['location']['lng']}"

    if len(route) > 1:
        waypoints = "|".join([f"{p['location']['lat']},{p['location']['lng']}" for p in route[:-1]])
        maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={destination}&waypoints={waypoints}&travelmode=walking"
    else:
        maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={destination}&travelmode=walking"

    keyboard = [
        [InlineKeyboardButton("🗺️ Відкрити навігатор", url=maps_url)],
        [InlineKeyboardButton("🔄 Новий маршрут", callback_data="new_route")],
        [InlineKeyboardButton("📍 Змінити локацію", callback_data="change_location")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message.reply_text(route_text, reply_markup=reply_markup, parse_mode='Markdown')


async def handle_route_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "new_route":
        context.user_data.pop('selected_categories', None)
        context.user_data.pop('route', None)
        keyboard = [
            [InlineKeyboardButton("🍕 Чогось би поїсти...", callback_data="mood_hungry")],
            [InlineKeyboardButton("🚀 Готовий до пригод!", callback_data="mood_adventurous")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Як ваш настрій зараз? 😊", reply_markup=reply_markup)
        return MOOD

    elif query.data == "change_location":
        keyboard = [[KeyboardButton("📍 Поділитися локацією", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text("📍 Чекаю нову локацію:", reply_markup=reply_markup)
        return LOCATION


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 До побачення! Тисніть /start для відновлення.")
    return ConversationHandler.END


def main():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("Помилка: TELEGRAM_BOT_TOKEN не знайдено в .env")
        return

    application = Application.builder().token(bot_token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            LOCATION: [MessageHandler(filters.LOCATION, handle_location)],
            MOOD: [CallbackQueryHandler(handle_mood, pattern="^mood_")],
            SELECTING_PLACES: [CallbackQueryHandler(handle_category_selection)],
            ROUTE: [CallbackQueryHandler(handle_route_actions)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)

    print("🌍 Smart Tourist Guide запущено!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()