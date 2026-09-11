import urllib.request
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000"

def api_get(endpoint):
    req = urllib.request.Request(f"{BASE_URL}{endpoint}")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def api_post(endpoint, data):
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def api_put(endpoint, data):
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT"
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def api_delete(endpoint):
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        method="DELETE"
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def run_tests():
    print("========================================")
    print("RUNNING E2E VERIFICATION SUITE")
    print("========================================")

    # 1. Check Stats
    stats = api_get("/api/admin/stats")
    print("1. Admin Stats:", stats)
    assert stats["total_buttons"] > 0, "Buttons should exist"
    assert stats["total_users"] > 0, "Users should exist"

    # 2. Test Dynamic Menu Builder: Create Button
    new_btn_data = {
        "menu_key": "main_menu",
        "parent_menu": "root",
        "label_uz": "Kafeteriya & Oshxona",
        "label_ru": "Кафетерий и столовая",
        "label_en": "Cafeteria & Dining",
        "emoji": "☕",
        "button_type": "send_message",
        "required_role": "all",
        "is_enabled": 1,
        "guest_access": 1,
        "student_access": 1,
        "guest_limit": 0,
        "student_limit": 0,
        "action_payload": "cafeteria_info"
    }
    create_res = api_post("/api/admin/buttons", new_btn_data)
    btn_id = create_res["id"]
    print(f"2. Created button ID: {btn_id}")

    # Verify button in menu
    menu_buttons = api_get("/api/admin/menus?menu_key=main_menu")
    found = any(b["id"] == btn_id for b in menu_buttons)
    assert found, "Created button must be present in menu"
    print("   Verified button exists in main_menu!")

    # 3. Update Button
    new_btn_data["label_uz"] = "Kafeteriya (Yangilandi)"
    api_put(f"/api/admin/buttons/{btn_id}", new_btn_data)
    updated_buttons = api_get("/api/admin/menus?menu_key=main_menu")
    upd_btn = next(b for b in updated_buttons if b["id"] == btn_id)
    assert upd_btn["label_uz"] == "Kafeteriya (Yangilandi)", "Button label should be updated"
    print("3. Verified button update successfully!")

    # 4. Delete Button
    del_res = api_delete(f"/api/admin/buttons/{btn_id}")
    print("4. Deleted test button:", del_res)

    # 5. Test Student Bot Flow: Help Message
    msg_flow_1 = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "✍️ Xabar yozish"
    })
    print("5. Clicked '✍️ Xabar yozish':", msg_flow_1["response"]["text"][:40])

    msg_flow_2 = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "🆘 Yordam kerak"
    })
    print("   Selected '🆘 Yordam kerak':", msg_flow_2["response"]["text"][:40])

    msg_flow_3 = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "Yotoqxona bo'yicha yordam bering, navbatimni qanday bilaman?"
    })
    print("   Sent text:", msg_flow_3["response"]["text"][:60])
    assert "qabul qilindi" in msg_flow_3["response"]["text"], "Should confirm receipt"

    # 6. Verify message in Admin Inbox
    inbox = api_get("/api/admin/messages?msg_type=help")
    assert len(inbox) > 0, "Inbox should have the new message"
    latest_msg = inbox[0]
    print(f"6. Latest Inbox Message: #{latest_msg['id']} from {latest_msg['user_name']}: '{latest_msg['content']}'")

    # 7. Admin Reply to message
    reply_res = api_post(f"/api/admin/messages/{latest_msg['id']}/reply", {
        "reply_text": "Hurmatli talaba, yotoqxona arizangiz TTJ komissiyasi tomonidan ko'rib chiqilmoqda. Natija HEMIS tizimida e'lon qilinadi."
    })
    print("7. Admin replied to student:", reply_res)

    # 8. Test Schedule Flow
    sch_res = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "🗓 Dars jadvali"
    })
    sch_today = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "📅 Bugun"
    })
    print("8. Schedule Response Preview:\n" + sch_today["response"]["text"][:120] + "...")

    # 9. Test Interactive AI Assistant
    ai_open = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "🤖 AI Yordamchi"
    })
    ai_ask = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "💬 Savol berish"
    })
    ai_query = api_post("/api/bot/action", {
        "user_id": 7058416364,
        "action_text": "Kontrakt to'lovini qanday to'layman?"
    })
    print("9. AI Assistant Response Preview:\n" + ai_query["response"]["text"][:140] + "...")
    assert "HEMIS" in ai_query["response"]["text"] or "to'lov" in ai_query["response"]["text"].lower(), "AI should answer university question"

    # 10. Test Guest Mode Restrictions
    guest_switch = api_post("/api/bot/switch-role", {
        "user_id": 999999999,
        "role": "guest"
    })
    guest_state = api_get("/api/bot/state/999999999")
    guest_buttons = [btn["display_text"] for row in guest_state["keyboard"] for btn in row]
    print("10. Guest Menu Buttons:", guest_buttons)
    # Check that student-only buttons are NOT in guest menu
    assert not any("O'qishim" in b or "Testlar" in b or "Mening profilim" in b for b in guest_buttons), "Guest must not see student-only buttons!"
    print("    Verified: Student-only buttons (O'qishim, Testlar, Profil) are completely hidden from Guest!")

    print("\n========================================")
    print("ALL VERIFICATION TESTS PASSED SUCCESSFULLY! ✅")
    print("========================================")

if __name__ == "__main__":
    run_tests()
