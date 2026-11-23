pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic

ApplicationWindow {
    id: preview
    width: 1160
    height: 740
    minimumWidth: 1160
    minimumHeight: 740
    title: lightPreview ? "Ритм — макет светлой темы" : "Ритм — макет тёмной темы"
    color: preview.theme.base

    property int variant: 0
    property bool lightPreview: false
    property bool reduceMotion: false
    property int selectedTask: 1
    property bool editingTask: false
    property string uiFont
    readonly property var themes: lightPreview ? [
        {base: "#f4f7f6", side: "#e8efed", panel: "#ffffff", field: "#fcfefd", line: "#cad8d3", control: "#788a84",
         text: "#182d28", muted: "#4b635c", accent: "#235c49", onAccent: "#ffffff",
         selected: "#dfefe8", hover: "#d5e9e0", warning: "#923d2e", radius: 12, dialog: 20},
        {base: "#f8f4ec", side: "#efe7da", panel: "#fffcf6", field: "#fffcf6", line: "#dacdbb", control: "#8c7a67",
         text: "#362b22", muted: "#67574a", accent: "#7a442b", onAccent: "#ffffff",
         selected: "#f3e3d4", hover: "#ecdbc9", warning: "#963c36", radius: 12, dialog: 20},
        {base: "#f1f5fb", side: "#e3ebf7", panel: "#ffffff", field: "#fcfdff", line: "#cbd7e7", control: "#7687a1",
         text: "#192c47", muted: "#4c6080", accent: "#345689", onAccent: "#ffffff",
         selected: "#dfeaf9", hover: "#d1e1f5", warning: "#913c53", radius: 12, dialog: 20}
    ] : [
        {base: "#171b1e", side: "#121618", panel: "#202629", field: "#161b1e", line: "#343d41", control: "#687679",
         text: "#f0f4f3", muted: "#abb8b9", accent: "#9de0c6", onAccent: "#10251e",
         selected: "#253933", hover: "#2a3336", warning: "#ffb5a7", radius: 12, dialog: 20},
        {base: "#1b2031", side: "#151a29", panel: "#242b40", field: "#1b2133", line: "#3b4664", control: "#6e7c9f",
         text: "#f0f1fb", muted: "#b0bad4", accent: "#c4ccff", onAccent: "#21294a",
         selected: "#303c5b", hover: "#303950", warning: "#ffb8c5", radius: 14, dialog: 24},
        {base: "#211e1b", side: "#1a1815", panel: "#2a2520", field: "#211d19", line: "#493e33", control: "#80705e",
         text: "#f7efe3", muted: "#c7b8a6", accent: "#f1c48b", onAccent: "#332014",
         selected: "#3b3025", hover: "#352e26", warning: "#ffb1a3", radius: 10, dialog: 18}
    ]
    readonly property var theme: themes[variant]
    readonly property var navigation: [
        {icon: "tasks", label: "Задачи"}, {icon: "calendar", label: "Календарь"},
        {icon: "chart", label: "Статистика"}, {icon: "book", label: "Материалы"}
    ]
    readonly property var tasks: [
        {name: "Сдать план доклада", category: "Учёба", deadline: "Просрочено · 20 ноя", progress: 75,
         estimate: "40 мин", difficulty: "4 из 10", detail: "Собрать тезисы и отправить план преподавателю.",
         steps: ["Собрать тезисы", "Отправить план"]},
        {name: "Подготовить отчёт по практике", category: "Учёба", deadline: "Сегодня · 18:00", progress: 60,
         estimate: "2 ч", difficulty: "6 из 10", detail: "Собрать результаты практики и оформить выводы.",
         steps: ["Собрать материалы", "Оформить результаты", "Написать выводы"]},
        {name: "Разобрать конспект лекции", category: "Учёба", deadline: "Завтра · 12:00", progress: 25,
         estimate: "45 мин", difficulty: "3 из 10", detail: "Выделить определения и отметить вопросы к семинару.",
         steps: ["Выделить определения", "Записать вопросы"]},
        {name: "Обновить список литературы", category: "Личное", deadline: "Без дедлайна", progress: 0,
         estimate: "30 мин", difficulty: "2 из 10", detail: "Добавить книги и статьи из последних конспектов.",
         steps: ["Собрать ссылки", "Добавить записи"]}
    ]
    readonly property var task: tasks[selectedTask]

    function openEditor(edit: bool): void {
        editingTask = edit
        titleField.text = edit ? task.name : ""
        editor.open()
        titleField.forceActiveFocus()
    }

    Shortcut { sequence: "Ctrl+N"; enabled: !editor.visible; onActivated: preview.openEditor(false) }
    Shortcut { sequence: "1"; enabled: !editor.visible; onActivated: preview.variant = 0 }
    Shortcut { sequence: "2"; enabled: !editor.visible; onActivated: preview.variant = 1 }
    Shortcut { sequence: "3"; enabled: !editor.visible; onActivated: preview.variant = 2 }
    Shortcut { sequence: "Ctrl+Shift+M"; onActivated: preview.reduceMotion = !preview.reduceMotion }

    component Copy: Text {
        color: preview.theme.text
        font.family: preview.uiFont
        font.pixelSize: 14
        renderType: Text.NativeRendering
        textFormat: Text.PlainText
    }

    component Glyph: Canvas {
        id: drawing
        property string symbol
        property color ink: preview.theme.muted
        width: 20
        height: 20
        onInkChanged: requestPaint()
        onSymbolChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.scale(width / 24, height / 24)
            ctx.strokeStyle = ink
            ctx.lineWidth = 1.65
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.beginPath()
            switch (symbol) {
            case "logo":
                ctx.moveTo(2, 13); ctx.lineTo(7, 13); ctx.lineTo(10, 5)
                ctx.lineTo(14, 19); ctx.lineTo(17, 11); ctx.lineTo(22, 11)
                break
            case "tasks":
                ctx.roundedRect(4, 4, 16, 16, 3, 3)
                ctx.moveTo(8, 12); ctx.lineTo(11, 15); ctx.lineTo(16, 9)
                break
            case "calendar":
                ctx.roundedRect(4, 6, 16, 14, 3, 3)
                ctx.moveTo(8, 4); ctx.lineTo(8, 8)
                ctx.moveTo(16, 4); ctx.lineTo(16, 8)
                ctx.moveTo(4, 11); ctx.lineTo(20, 11)
                break
            case "chart":
                ctx.moveTo(5, 19); ctx.lineTo(19, 19)
                ctx.moveTo(7, 15); ctx.lineTo(7, 11)
                ctx.moveTo(12, 15); ctx.lineTo(12, 6)
                ctx.moveTo(17, 15); ctx.lineTo(17, 9)
                break
            case "book":
                ctx.roundedRect(5, 4, 14, 16, 2, 2)
                ctx.moveTo(9, 4); ctx.lineTo(9, 20)
                ctx.moveTo(12, 9); ctx.lineTo(16, 9)
                break
            case "search":
                ctx.arc(10.5, 10.5, 5.5, 0, 2 * Math.PI)
                ctx.moveTo(15, 15); ctx.lineTo(20, 20)
                break
            case "plus":
                ctx.moveTo(6, 12); ctx.lineTo(18, 12)
                ctx.moveTo(12, 6); ctx.lineTo(12, 18)
                break
            case "close":
                ctx.moveTo(7, 7); ctx.lineTo(17, 17)
                ctx.moveTo(17, 7); ctx.lineTo(7, 17)
                break
            case "check":
                ctx.moveTo(5, 12); ctx.lineTo(9, 16); ctx.lineTo(19, 7)
                break
            case "time":
                ctx.arc(12, 12, 8, 0, 2 * Math.PI)
                ctx.moveTo(12, 7); ctx.lineTo(12, 12); ctx.lineTo(15, 14)
                break
            case "chevron":
                ctx.moveTo(8, 10); ctx.lineTo(12, 14); ctx.lineTo(16, 10)
                break
            }
            ctx.stroke()
        }
    }

    component Pill: Button {
        id: button
        property bool primary: false
        property bool selected: false
        property string glyph: ""
        property bool quiet: false
        property bool leftAligned: false
        property bool trailingGlyph: false
        readonly property color ink: primary ? preview.theme.onAccent : selected ? preview.theme.accent : preview.theme.text
        implicitHeight: 38
        leftPadding: leftAligned ? 11 : 13
        rightPadding: 13
        hoverEnabled: true
        contentItem: Row {
            spacing: 7
            layoutDirection: button.trailingGlyph ? Qt.RightToLeft : Qt.LeftToRight
            Glyph {
                visible: button.glyph !== ""
                symbol: button.glyph
                ink: button.ink
                anchors.verticalCenter: parent.verticalCenter
            }
            Copy {
                visible: button.text !== ""
                text: button.text
                color: button.ink
                font.pixelSize: 13
                font.weight: button.primary || button.selected ? Font.DemiBold : Font.Medium
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        background: Rectangle {
            radius: preview.theme.radius
            border.width: button.visualFocus ? 2 : button.quiet || button.primary || button.selected ? 0 : 1
            border.color: button.visualFocus ? preview.theme.accent : preview.theme.control
            color: button.primary ? (button.down ? Qt.darker(preview.theme.accent, 1.16)
                                                : button.hovered ? Qt.lighter(preview.theme.accent, 1.1) : preview.theme.accent)
                : button.down || button.selected ? preview.theme.selected
                : button.hovered ? preview.theme.hover : button.quiet ? "transparent" : preview.theme.panel
            Behavior on color {
                enabled: !preview.reduceMotion
                ColorAnimation { duration: 110 }
            }
        }
    }

    component ProgressTrack: Rectangle {
        id: track
        property int percent: 0
        height: 4
        radius: 2
        color: preview.theme.line
        Rectangle {
            width: track.width * track.percent / 100
            height: track.height
            radius: track.radius
            color: preview.theme.accent
        }
    }

    component Field: TextField {
        id: input
        font.family: preview.uiFont
        font.pixelSize: 14
        height: 41
        leftPadding: 12
        color: preview.theme.text
        placeholderTextColor: preview.theme.muted
        selectionColor: preview.theme.accent
        selectedTextColor: preview.theme.onAccent
        background: Rectangle {
            color: preview.theme.field
            radius: preview.theme.radius
            border.width: input.activeFocus ? 2 : 1
            border.color: input.activeFocus ? preview.theme.accent : preview.theme.control
        }
    }

    component TaskRow: Button {
        id: row
        required property int taskIndex
        readonly property var entry: preview.tasks[taskIndex]
        readonly property bool current: taskIndex === preview.selectedTask
        width: parent.width
        height: preview.variant === 1 ? 56 : 59
        hoverEnabled: true
        Accessible.name: entry.name
        onClicked: preview.selectedTask = taskIndex
        background: Rectangle {
            radius: preview.theme.radius
            color: row.current ? preview.theme.selected : row.hovered ? preview.theme.hover
                : preview.dividedList || !preview.lightPreview && preview.variant === 1 ? "transparent" : preview.theme.panel
            border.width: row.visualFocus ? 2 : row.current && !preview.dividedList ? 1 : 0
            border.color: row.visualFocus || preview.lightPreview && row.current ? preview.theme.accent : preview.theme.line
            Behavior on color {
                enabled: !preview.reduceMotion
                ColorAnimation { duration: 120 }
            }
            Rectangle {
                visible: preview.dividedList && !row.current
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: preview.theme.line
            }
        }
        contentItem: Item {
            Rectangle {
                x: 10; y: 12; width: 16; height: 16
                radius: 5
                color: "transparent"
                border.color: row.current ? preview.theme.accent : preview.theme.muted
            }
            Copy {
                x: 39; y: 9; width: parent.width - 160
                text: row.entry.name
                elide: Text.ElideRight
                font.weight: Font.DemiBold
            }
            Copy {
                x: 39; y: 32
                text: row.entry.category + "  ·  " + row.entry.deadline
                color: row.taskIndex === 0 ? preview.theme.warning : preview.theme.muted
                font.pixelSize: 12
            }
            Copy {
                anchors.right: parent.right; anchors.rightMargin: 11; y: 9
                text: row.entry.progress + "%"
                color: row.current ? preview.theme.accent : preview.theme.muted
                font.pixelSize: 12
            }
            ProgressTrack {
                anchors.right: parent.right; anchors.rightMargin: 11; y: 34
                width: 63
                percent: row.entry.progress
            }
        }
    }

    readonly property bool tabs: !lightPreview && variant === 1
    readonly property bool dividedList: variant === (lightPreview ? 1 : 2)
    readonly property int sideWidth: dividedList ? 160 : 184

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        width: parent.width
        height: 58
        color: preview.theme.side
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: preview.theme.line }
        Rectangle {
            x: 23; y: 14; width: 30; height: 30
            radius: 9
            color: preview.theme.selected
            Glyph { anchors.centerIn: parent; symbol: "logo"; ink: preview.theme.accent }
        }
        Copy { x: 64; anchors.verticalCenter: parent.verticalCenter; text: "Ритм"; font.pixelSize: 18; font.weight: Font.DemiBold }
        Row {
            x: 185
            anchors.verticalCenter: parent.verticalCenter
            spacing: 8
            visible: preview.tabs
            Repeater {
                model: preview.navigation
                Pill {
                    required property int index
                    required property var modelData
                    glyph: modelData.icon
                    text: modelData.label
                    selected: index === 0
                    quiet: true
                }
            }
        }
        Row {
            anchors.right: parent.right; anchors.rightMargin: 23; anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            Pill { text: "Корзина"; quiet: true; visible: preview.tabs }
            Pill { text: "Помощник"; quiet: true }
            Pill { text: "Вид"; quiet: true; glyph: "chevron"; trailingGlyph: true }
        }
    }

    Rectangle {
        x: 0; y: 58
        width: preview.sideWidth
        height: preview.height - y
        color: preview.theme.side
        visible: !preview.tabs
        Rectangle { anchors.right: parent.right; height: parent.height; width: 1; color: preview.theme.line }
        Column {
            x: 13; y: 18; width: parent.width - 26
            spacing: 5
            Repeater {
                model: preview.navigation
                Pill {
                    required property int index
                    required property var modelData
                    width: parent.width
                    leftAligned: true
                    glyph: modelData.icon
                    text: modelData.label
                    quiet: true
                    selected: index === 0
                }
            }
        }
        Pill {
            x: 13; anchors.bottom: parent.bottom; anchors.bottomMargin: 23
            width: parent.width - 26
            leftAligned: true
            quiet: true
            text: "Корзина"
        }
    }

    Item {
        id: workspace
        x: preview.tabs ? 34 : preview.sideWidth + 26
        y: 82
        width: preview.width - x - (preview.tabs ? 34 : 25)
        height: preview.height - y - 26

        Copy { y: 0; text: "Мои задачи"; font.pixelSize: 30; font.weight: Font.DemiBold }
        Copy { y: 43; text: "Суббота, 21 ноября"; color: preview.theme.muted; font.pixelSize: 13 }
        Pill {
            id: newTask
            objectName: "newTask"
            anchors.right: parent.right
            y: 6
            glyph: "plus"
            text: "Задача"
            primary: true
            onClicked: preview.openEditor(false)
        }

        Rectangle {
            id: week
            y: 77
            width: parent.width
            height: preview.tabs ? 82 : 68
            color: preview.dividedList ? "transparent"
                : preview.lightPreview && preview.variant === 2 ? preview.theme.side : preview.theme.panel
            radius: preview.theme.radius
            border.width: preview.dividedList ? 0 : 1
            border.color: preview.theme.line
            Rectangle {
                visible: preview.dividedList
                anchors.bottom: parent.bottom; width: parent.width; height: 1; color: preview.theme.line
            }
            Copy {
                x: preview.dividedList ? 0 : 16
                y: preview.tabs ? 16 : 11
                text: "Прогресс недели"
                font.weight: Font.DemiBold
            }
            Copy {
                x: preview.dividedList ? 0 : 16
                y: preview.tabs ? 41 : 34
                text: "16–22 ноября  ·  4 задачи со сроком"
                font.pixelSize: 12
                color: preview.theme.muted
            }
            Item {
                width: preview.tabs ? 275 : 182
                height: 37
                anchors.right: parent.right; anchors.rightMargin: preview.dividedList ? 0 : 16
                anchors.verticalCenter: parent.verticalCenter
                Copy { anchors.right: parent.right; text: "65%"; color: preview.theme.accent; font.pixelSize: 16; font.weight: Font.DemiBold }
                ProgressTrack { width: parent.width; y: 28; percent: 65 }
            }
        }

        Row {
            id: filters
            y: week.y + week.height + 15
            spacing: 8
            Field { width: preview.tabs ? 306 : 222; placeholderText: "Поиск задач"; leftPadding: 35
                Glyph { x: 10; anchors.verticalCenter: parent.verticalCenter; symbol: "search" } }
            Pill { height: 41; text: "Активные"; glyph: "chevron"; trailingGlyph: true }
            Pill { height: 41; text: "Все категории"; glyph: "chevron"; trailingGlyph: true }
        }

        Column {
            id: listing
            y: filters.y + filters.height + 14
            width: parent.width - details.width - 22
            spacing: 7
            Copy { text: "Просроченные"; font.pixelSize: 13; font.weight: Font.DemiBold; color: preview.theme.warning }
            TaskRow { taskIndex: 0 }
            Item { width: 1; height: 2 }
            Copy { text: "Предстоящие"; font.pixelSize: 13; font.weight: Font.DemiBold; color: preview.theme.muted }
            TaskRow { taskIndex: 1 }
            TaskRow { taskIndex: 2 }
            Item { width: 1; height: 2 }
            Copy { text: "Без дедлайна"; font.pixelSize: 13; font.weight: Font.DemiBold; color: preview.theme.muted }
            TaskRow { taskIndex: 3 }
        }

        Rectangle {
            id: details
            anchors.right: parent.right
            y: listing.y
            width: preview.dividedList ? 366 : preview.tabs ? 360 : 342
            height: parent.height - y
            color: preview.theme.panel
            radius: preview.theme.radius
            border.width: preview.dividedList ? 0 : 1
            border.color: preview.theme.line
            Column {
                x: 18; y: 15; width: parent.width - 36
                spacing: 10
                Copy {
                    width: parent.width
                    text: preview.task.name
                    wrapMode: Text.WordWrap
                    font.pixelSize: 22
                    font.weight: Font.DemiBold
                }
                Copy {
                    text: preview.task.category + "  ·  " + preview.task.deadline
                    color: preview.selectedTask === 0 ? preview.theme.warning : preview.theme.muted
                    font.pixelSize: 12
                }
                Copy {
                    width: parent.width
                    text: preview.task.detail
                    wrapMode: Text.WordWrap
                    color: preview.theme.muted
                }
                Rectangle {
                    width: parent.width
                    height: 1
                    color: preview.theme.line
                }
                Row {
                    spacing: 32
                    Column {
                        spacing: 3
                        Copy { text: "Ожидаемое время"; color: preview.theme.muted; font.pixelSize: 12 }
                        Copy { text: preview.task.estimate; font.weight: Font.DemiBold }
                    }
                    Column {
                        spacing: 3
                        Copy { text: "Сложность"; color: preview.theme.muted; font.pixelSize: 12 }
                        Copy { text: preview.task.difficulty; font.weight: Font.DemiBold }
                    }
                }
                Copy { text: "Подзадачи"; font.weight: Font.DemiBold }
                Column {
                    spacing: 8
                    Repeater {
                        model: preview.task.steps
                        Row {
                            id: step
                            required property int index
                            required property string modelData
                            spacing: 8
                            Rectangle {
                                width: 16; height: 16; radius: 5
                                anchors.verticalCenter: parent.verticalCenter
                                color: step.index === 0 ? preview.theme.selected : "transparent"
                                border.color: step.index === 0 ? preview.theme.accent : preview.theme.muted
                                Glyph {
                                    anchors.centerIn: parent; width: 13; height: 13
                                    visible: step.index === 0
                                    symbol: "check"; ink: preview.theme.accent
                                }
                            }
                            Copy { text: step.modelData; font.pixelSize: 13 }
                        }
                    }
                }
            }
            Pill {
                x: 18
                anchors.bottom: parent.bottom; anchors.bottomMargin: 14
                glyph: "time"
                text: "Начать фокус"
            }
            Pill {
                anchors.right: parent.right; anchors.rightMargin: 14
                anchors.bottom: parent.bottom; anchors.bottomMargin: 14
                text: "Изменить"
                quiet: true
                onClicked: preview.openEditor(true)
            }
        }
    }

    Overlay.modal: Rectangle { color: preview.lightPreview ? "#62182a33" : "#980a0b0e" }

    Popup {
        id: editor
        objectName: "previewEditor"
        parent: Overlay.overlay
        x: (preview.width - width + (preview.lightPreview ? preview.sideWidth : 0)) / 2
        y: (preview.height - height) / 2
        padding: 24
        width: preview.tabs ? 544 : 522
        height: 621
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        background: Rectangle {
            color: preview.theme.panel
            radius: preview.theme.dialog
            border.color: preview.theme.line
        }
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: preview.reduceMotion ? 0 : 150 }
                NumberAnimation { property: "scale"; from: 0.98; to: 1; duration: preview.reduceMotion ? 0 : 170; easing.type: Easing.OutCubic }
            }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1; to: 0; duration: preview.reduceMotion ? 0 : 105 }
        }
        Item {
            anchors.fill: parent
            Copy { text: preview.editingTask ? "Изменить задачу" : "Новая задача"; font.pixelSize: 25; font.weight: Font.DemiBold }
            Pill {
                anchors.top: parent.top; anchors.right: parent.right
                width: 42
                height: 32
                glyph: "close"
                quiet: true
                Accessible.name: "Закрыть"
                onClicked: editor.close()
            }
            Column {
                y: 57
                width: parent.width
                spacing: 7
                Copy { text: "Название *"; font.weight: Font.DemiBold }
                Field { id: titleField; objectName: "previewTitle"; width: parent.width; placeholderText: "Например, подготовить отчёт" }
                Item { height: 4; width: 1 }
                Copy { text: "Категория"; font.weight: Font.DemiBold }
                Field { width: parent.width; placeholderText: "Выберите или введите новую"
                    Glyph { anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; symbol: "chevron" } }
                Item { height: 4; width: 1 }
                Copy { text: "Описание"; font.weight: Font.DemiBold }
                TextArea {
                    id: description
                    width: parent.width
                    height: 65
                    padding: 12
                    font.family: preview.uiFont
                    font.pixelSize: 14
                    color: preview.theme.text
                    placeholderText: "Что нужно сделать?"
                    placeholderTextColor: preview.theme.muted
                    wrapMode: TextEdit.Wrap
                    background: Rectangle {
                        color: preview.theme.field
                        radius: preview.theme.radius
                        border.width: description.activeFocus ? 2 : 1
                        border.color: description.activeFocus ? preview.theme.accent : preview.theme.control
                    }
                }
                Item { height: 4; width: 1 }
                Row {
                    width: parent.width
                    spacing: 12
                    Column {
                        width: (parent.width - 12) / 2
                        spacing: 6
                        Copy { text: "Сложность · 1–10"; font.weight: Font.DemiBold }
                        Field { width: parent.width; placeholderText: "Не задана" }
                    }
                    Column {
                        width: (parent.width - 12) / 2
                        spacing: 6
                        Copy { text: "Время · минуты"; font.weight: Font.DemiBold }
                        Field { width: parent.width; placeholderText: "Не задано" }
                    }
                }
                Item { height: 4; width: 1 }
                Copy { text: "Дедлайн · местное время"; font.weight: Font.DemiBold }
                Field { width: parent.width; placeholderText: "ДД.ММ.ГГГГ ЧЧ:ММ"
                    Glyph { anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; symbol: "calendar" } }
                Copy { text: "Обязательно только название."; color: preview.theme.muted; font.pixelSize: 12 }
            }
            Rectangle {
                width: parent.width
                height: 1
                anchors.bottom: actions.top
                anchors.bottomMargin: 13
                color: preview.theme.line
            }
            Row {
                id: actions
                anchors.bottom: parent.bottom
                anchors.right: parent.right
                spacing: 8
                Pill { text: "Отмена"; onClicked: editor.close() }
                Pill { text: "Сохранить"; primary: true; onClicked: editor.close() }
            }
        }
    }
}
