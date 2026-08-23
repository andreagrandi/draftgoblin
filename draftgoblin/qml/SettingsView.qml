pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root

    required property var sessionState
    required property bool narrow

    ScrollView {
        id: settingsScroll
        contentWidth: availableWidth
        anchors.fill: parent
        clip: true

        ColumnLayout {
            width: settingsScroll.availableWidth
            spacing: Theme.gutter

            Label {
                text: "Settings"
                color: Theme.text
                font.pixelSize: 22
                font.bold: true
            }

            Label {
                Layout.fillWidth: true
                text: "Draft guidance and display preferences for the desktop application."
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: guidanceLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius

                ColumnLayout {
                    id: guidanceLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 14

                    Label {
                        text: "DRAFT GUIDANCE"
                        color: Theme.primary
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 1.1
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Default ranking"; color: Theme.text; font.bold: true }
                            Label { text: "Controls recommendation order and backtest comparison."; color: Theme.textMuted; font.pixelSize: 11 }
                        }

                        ComboBox {
                            id: defaultRanking
                            Layout.preferredWidth: 168
                            model: [
                                { key: "score", label: "DG Score" },
                                { key: "win_rate", label: "17L WR" },
                                { key: "alsa", label: "ALSA" },
                                { key: "mana_value", label: "Mana value" }
                            ]
                            textRole: "label"
                            currentIndex: {
                                for (let index = 0; index < model.length; index++)
                                    if (model[index].key === root.sessionState.recommendations.ranking_mode)
                                        return index
                                return 0
                            }
                            onActivated: sessionProvider.changeRanking(model[currentIndex].key)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Splash recommendations"; color: Theme.text; font.bold: true }
                            Label { text: "Consider supported single-pip cards when fixing allows."; color: Theme.textMuted; font.pixelSize: 11 }
                        }

                        Switch {
                            checked: root.sessionState.recommendations.splash_enabled
                            Accessible.name: "Splash recommendations"
                            onToggled: sessionProvider.setSplashEnabled(checked)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: displayLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius

                ColumnLayout {
                    id: displayLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 12

                    Label {
                        text: "DISPLAY"
                        color: Theme.primary
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 1.1
                    }

                    Repeater {
                        model: [
                            { label: "Compact density", detail: "Reduce row spacing while retaining keyboard targets.", checked: false },
                            { label: "Secondary statistics", detail: "Show ALSA, mana value, and source details.", checked: true },
                            { label: "Card image preview", detail: "Keep the selected card image visible when space allows.", checked: true },
                            { label: "Detailed build context", detail: "Show pair reasoning and durable build warnings.", checked: true }
                        ]

                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true

                            ColumnLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.label; color: Theme.text; font.bold: true }
                                Label { text: modelData.detail; color: Theme.textMuted; font.pixelSize: 11 }
                            }
                            Switch {
                                checked: modelData.checked
                                Accessible.name: modelData.label
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: accessibilityLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius

                ColumnLayout {
                    id: accessibilityLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8

                    Label {
                        text: "ACCESSIBILITY"
                        color: Theme.primary
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 1.1
                    }
                    Label { text: "System text scaling"; color: Theme.text; font.bold: true }
                    Label {
                        Layout.fillWidth: true
                        text: "Draftgoblin follows Qt and operating-system text and reduced-motion settings."
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: dataLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius

                ColumnLayout {
                    id: dataLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    Label {
                        text: "DATA STATUS"
                        color: Theme.primary
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 1.1
                    }
                    Label { text: "Card metadata · " + root.sessionState.card_data.message; color: Theme.text }
                    Label { text: "Ratings · " + root.sessionState.ratings.message; color: Theme.textMuted }
                    Label { text: "Statistics attribution · 17Lands"; color: Theme.textMuted }
                    Button {
                        objectName: "settingsRatingsDownloadButton"
                        enabled: root.sessionState.ratings.set_code !== null
                            && root.sessionState.ratings.set_code !== undefined
                            && root.sessionState.ratings.phase !== "loading"
                        text: "Download or refresh ratings"
                        Accessible.name: "Download or refresh ratings"
                        onClicked: ratingsDownloadDialog.open()
                    }
                }
            }

            Item {
                Layout.preferredHeight: 12
            }
        }
    }

    Dialog {
        id: ratingsDownloadDialog
        objectName: "settingsRatingsDownloadDialog"
        implicitWidth: 400
        modal: true
        parent: Overlay.overlay
        title: "Download ratings?"

        Label {
            width: 360
            text: "Download 17Lands ratings for "
                + root.sessionState.ratings.set_code
                + "? Neutral-prior recommendations remain available while it loads."
            color: Theme.text
            wrapMode: Text.WordWrap
        }

        footer: DialogButtonBox {
            Button {
                objectName: "settingsRatingsDownloadCancelButton"
                text: "Not now"
                Accessible.name: "Cancel ratings download"
                onClicked: ratingsDownloadDialog.close()
            }

            Button {
                objectName: "settingsRatingsDownloadConfirmButton"
                text: "Download ratings"
                Accessible.name: "Confirm ratings download"
                onClicked: {
                    sessionProvider.requestRatings()
                    ratingsDownloadDialog.close()
                }
            }
        }
    }
}

