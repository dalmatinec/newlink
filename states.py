from aiogram.fsm.state import State, StatesGroup


class SettingsStates(StatesGroup):
    waiting_greeting = State()


class ButtonStates(StatesGroup):
    waiting_label = State()
    waiting_type = State()
    waiting_target = State()          # url / callback data / текст для copy
    waiting_chat_source = State()     # источник чата для generated-кнопки
    waiting_invite_mode = State()
    waiting_menu_target = State()     # id меню, на которое ведёт кнопка-меню
    waiting_row = State()
    waiting_icon = State()            # premium emoji на кнопку (опционально)
    waiting_delete_number = State()
    waiting_move_number = State()
    waiting_move_target = State()     # "row position"


class AdStates(StatesGroup):
    choosing_slot = State()
    waiting_source = State()          # форвард или ручной ввод
    waiting_content = State()
    waiting_buttons_choice = State()
    waiting_schedule_mode = State()
    waiting_schedule_value = State()  # часов/раз в день, в зависимости от режима


class BroadcastStates(StatesGroup):
    waiting_source = State()
    waiting_content = State()
    waiting_buttons_choice = State()
    waiting_confirm = State()
