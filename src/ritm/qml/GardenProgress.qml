pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RitmDialog {
    id: dialog
    required property var backend
    readonly property var achievements: backend.garden.progress
    property string selectedMonth: ""
    readonly property var periods: achievements.periods || []
    readonly property var period: periods.find(p => p.month === selectedMonth) || ({
                                                                                       goals: []
                                                                                   })
    objectName: "gardenProgressDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    title: qsTr("Достижения сада")
    width: Math.min(appearance.dp(600), parent.width - 24)
    height: Math.min(appearance.dp(720), parent.height - 24)
    modal: true
    closePolicy: Popup.CloseOnEscape
    function resetMonth() {
        if (!periods.some(p => p.month === selectedMonth))
            selectedMonth = achievements.currentMonth || "";
    }
    onAchievementsChanged: resetMonth()
    onOpened: {
        resetMonth();
        monthChoice.forceActiveFocus();
    }
    contentItem: ScrollView {
        id: scroll
        objectName: "gardenProgressScroll"
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: scroll.availableWidth
            spacing: dialog.appearance.dp(12)
            Label {
                objectName: "gardenProgressUnavailable"
                Layout.fillWidth: true
                visible: !!dialog.backend.garden.error
                text: dialog.backend.garden.error
                color: dialog.appearance.colors.warning
                wrapMode: Text.Wrap
            }
            ColumnLayout {
                visible: !dialog.backend.garden.error
                Layout.fillWidth: true
                spacing: dialog.appearance.dp(12)
                Label {
                    objectName: "gardenWorkProgress"
                    Layout.fillWidth: true
                    text: qsTr("Выполнено главных задач: %1. Оплачено часов: %2.").arg(
                              dialog.backend.garden.roots).arg(dialog.backend.garden.paidHours)
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: dialog.backend.garden.enabled ? qsTr("Учёт включён.") : qsTr(
                                                              "Учёт выключен. Полученное сохранено; работа во время отключения не учитывается.")
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Месячные цели")
                    font.bold: true
                }
                RitmComboBox {
                    id: monthChoice
                    appearance: dialog.appearance
                    objectName: "gardenProgressMonth"
                    Layout.fillWidth: true
                    model: dialog.periods
                    textRole: "title"
                    currentIndex: dialog.periods.findIndex(p => p.month === dialog.selectedMonth)
                    Accessible.name: qsTr("Месяц достижений")
                    onActivated: index => dialog.selectedMonth = dialog.periods[index].month
                }
                Label {
                    objectName: "gardenMonthEarned"
                    Layout.fillWidth: true
                    text: qsTr("Получено за цели: %1 / %2 монет").arg(dialog.period.earned || 0).arg(
                              dialog.period.maximum || 1000)
                    wrapMode: Text.Wrap
                }
                Repeater {
                    model: dialog.period.goals
                    delegate: Frame {
                        id: goalCard
                        required property var modelData
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            Label {
                                objectName: "gardenGoal_" + goalCard.modelData.key
                                Layout.fillWidth: true
                                text: goalCard.modelData.title + " · " + goalCard.modelData.progress
                                wrapMode: Text.Wrap
                            }
                            ProgressBar {
                                Layout.fillWidth: true
                                value: goalCard.modelData.ratio
                                Accessible.name: goalCard.modelData.title
                            }
                            Label {
                                Layout.fillWidth: true
                                text: goalCard.modelData.done ? qsTr("Получено: %1 монет").arg(
                                                                    goalCard.modelData.reward) :
                                                                qsTr("Награда: %1 монет · ещё не получена").arg(
                                                                    goalCard.modelData.reward)
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr(
                              "Считаются уникальные главные задачи и оплаченные часы. Перерывы, долг, время сверх дневного предела и выключенного учёта не добавляют оплаченных часов. Новые цели начинаются каждый месяц; прежние награды сохраняются.")
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Постоянные достижения")
                    font.bold: true
                }
                Repeater {
                    model: dialog.achievements.permanent || []
                    delegate: Frame {
                        id: permanentCard
                        required property var modelData
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            Label {
                                objectName: "gardenPermanent_" + permanentCard.modelData.key
                                Layout.fillWidth: true
                                text: permanentCard.modelData.title + (permanentCard.modelData.done
                                                                       ? qsTr(" · получено") : qsTr(
                                                                             " · ещё не получено"))
                                font.bold: true
                                wrapMode: Text.Wrap
                            }
                            Label {
                                Layout.fillWidth: true
                                text: permanentCard.modelData.condition
                                wrapMode: Text.Wrap
                            }
                            Label {
                                Layout.fillWidth: true
                                text: permanentCard.modelData.reward
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr(
                              "Светлая и тёмная темы доступны сразу. Открытые темы выбираются в разделе «Вид» и сохраняются при выключении сада.")
                    wrapMode: Text.Wrap
                }
            }
        }
    }
    footer: RitmDialogFooter {
        appearance: dialog.appearance
        RitmButton {
            appearance: dialog.appearance
            objectName: "closeGardenProgress"
            text: qsTr("Закрыть")
            onClicked: dialog.close()
        }
    }
}
